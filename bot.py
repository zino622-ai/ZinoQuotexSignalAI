 
import os
import json
import time
import math
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = os.getenv("OWNER_ID", "").strip()
MT4_API_KEY = os.getenv("MT4_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

PORT = int(os.getenv("PORT", "10000"))

TIMEZONE = ZoneInfo("Africa/Algiers")

SIGNAL_INTERVAL_SECONDS = 180
TRADE_DURATION_SECONDS = 60
STOP_MONITOR_SECONDS = 5
MARKET_DATA_MAX_AGE_SECONDS = 20
MAX_CANDLES = 200

# Do not change these to zero to force more signals.
MIN_CONFIDENCE = 55
MAX_CONFIDENCE = 88

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("ZinoProSignalAI")

# ============================================================
# GLOBAL STATE
# ============================================================

try:
    OWNER_ID_INT = int(OWNER_ID) if OWNER_ID else 0
except ValueError:
    OWNER_ID_INT = 0

telegram_app = None
telegram_loop = None
genai_client = None

state_lock = threading.RLock()
analysis_lock = threading.Lock()

latest_market = {}
processed_signals = set()

active_trade = None
last_signal_time = 0.0
last_signal_card = None

stats = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
}

# ============================================================
# GENERAL HELPERS
# ============================================================

def now_local():
    return datetime.now(TIMEZONE)


def now_text():
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def timeframe_seconds(value):
    value = str(value or "M1").upper().strip()

    mapping = {
        "M1": 60,
        "1M": 60,
        "1MIN": 60,
        "M2": 120,
        "2M": 120,
        "M3": 180,
        "3M": 180,
        "M5": 300,
        "5M": 300,
        "M15": 900,
        "15M": 900,
        "M30": 1800,
        "30M": 1800,
        "H1": 3600,
        "1H": 3600,
        "H4": 14400,
        "4H": 14400,
    }

    if value in mapping:
        return mapping[value]

    if value.isdigit():
        return max(60, int(value) * 60)

    return 60


def clean_number(value, default=None):
    try:
        result = float(value)
        if not math.isfinite(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def price_digits(price):
    if price is None:
        return 5

    text = f"{price:.10f}".rstrip("0").rstrip(".")

    if "." in text:
        return min(8, len(text.split(".")[1]))

    return 0


def format_price(price):
    if price is None:
        return "غير متوفر"

    digits = price_digits(price)

    return f"{price:.{digits}f}"


def normalize_candles(raw_candles):
    if not isinstance(raw_candles, list):
        return []

    result = []

    for candle in raw_candles[-MAX_CANDLES:]:
        if not isinstance(candle, dict):
            continue

        open_price = clean_number(
            candle.get("open", candle.get("o"))
        )
        high_price = clean_number(
            candle.get("high", candle.get("h"))
        )
        low_price = clean_number(
            candle.get("low", candle.get("l"))
        )
        close_price = clean_number(
            candle.get("close", candle.get("c"))
        )

        if None in (
            open_price,
            high_price,
            low_price,
            close_price,
        ):
            continue

        if (
            high_price < low_price
            or high_price < max(open_price, close_price)
            or low_price > min(open_price, close_price)
        ):
            continue

        timestamp = candle.get(
            "time",
            candle.get("timestamp", candle.get("t", 0)),
        )

        try:
            timestamp = int(float(timestamp))
        except (TypeError, ValueError):
            timestamp = 0

        result.append({
            "time": timestamp,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
        })

    # Remove duplicate candle timestamps.
    unique = {}

    for candle in result:
        key = candle["time"]

        if key:
            unique[key] = candle
        else:
            unique[f"untimed_{len(unique)}"] = candle

    result = list(unique.values())

    # Sort from oldest to newest when timestamps are available.
    if result and all(c["time"] > 0 for c in result):
        result.sort(key=lambda item: item["time"])

    return result[-MAX_CANDLES:]


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = value * multiplier + result * (1.0 - multiplier)

    return result


def calculate_rsi(values, period=14):
    if len(values) < period + 1:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss

    return 100.0 - (100.0 / (1.0 + rs))


def calculate_williams_r(candles, period=14):
    if len(candles) < period:
        return None

    recent = candles[-period:]

    highest = max(c["high"] for c in recent)
    lowest = min(c["low"] for c in recent)
    close = recent[-1]["close"]

    if highest == lowest:
        return -50.0

    return -100.0 * (highest - close) / (highest - lowest)


def calculate_adx_di(candles, period=14):
    if len(candles) < period + 2:
        return None, None, None

    plus_dm = []
    minus_dm = []
    true_ranges = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]

        up_move = current["high"] - previous["high"]
        down_move = previous["low"] - current["low"]

        plus_dm.append(
            up_move if up_move > down_move and up_move > 0 else 0.0
        )

        minus_dm.append(
            down_move if down_move > up_move and down_move > 0 else 0.0
        )

        true_range = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"]),
        )

        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return None, None, None

    atr = sum(true_ranges[:period]) / period
    plus = sum(plus_dm[:period]) / period
    minus = sum(minus_dm[:period]) / period

    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period
        plus = (plus * (period - 1) + plus_dm[i]) / period
        minus = (minus * (period - 1) + minus_dm[i]) / period

    if atr <= 0:
        return None, None, None

    plus_di = 100.0 * plus / atr
    minus_di = 100.0 * minus / atr

    denominator = plus_di + minus_di

    if denominator == 0:
        return 0.0, plus_di, minus_di

    dx_values = []

    # Approximate ADX using recent directional movement.
    start = max(0, len(true_ranges) - period)

    for i in range(start, len(true_ranges)):
        local_atr = max(true_ranges[i], 1e-12)
        local_plus = 100.0 * plus_dm[i] / local_atr
        local_minus = 100.0 * minus_dm[i] / local_atr

        total = local_plus + local_minus

        if total > 0:
            dx_values.append(
                100.0 * abs(local_plus - local_minus) / total
            )

    adx = sum(dx_values) / len(dx_values) if dx_values else 0.0

    return adx, plus_di, minus_di


def market_features(candles):
    closes = [c["close"] for c in candles]

    if len(closes) < 30:
        raise ValueError("At least 30 valid candles are required.")

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    rsi14 = calculate_rsi(closes, 14)
    williams = calculate_williams_r(candles, 14)
    adx, plus_di, minus_di = calculate_adx_di(candles, 14)

    last = candles[-1]
    previous = candles[-2]

    body = abs(last["close"] - last["open"])
    candle_range = max(last["high"] - last["low"], 1e-12)

    body_ratio = body / candle_range

    momentum = last["close"] - previous["close"]

    recent_high = max(c["high"] for c in candles[-10:-1])
    recent_low = min(c["low"] for c in candles[-10:-1])

    bullish_break = last["close"] > recent_high
    bearish_break = last["close"] < recent_low

    return {
        "last_close": last["close"],
        "last_open": last["open"],
        "last_high": last["high"],
        "last_low": last["low"],
        "previous_close": previous["close"],
        "ema9": ema9,
        "ema21": ema21,
        "rsi14": rsi14,
        "williams_r14": williams,
        "adx14": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "momentum": momentum,
        "body_ratio": body_ratio,
        "bullish_breakout": bullish_break,
        "bearish_breakout": bearish_break,
    }


# ============================================================
# SIGNAL ANALYSIS
# ============================================================

def fallback_signal(candles, features):
    """
    Local technical fallback used if Gemini is unavailable.
    It does not guarantee a winning trade.
    """

    up_score = 0
    down_score = 0
    reasons = []

    ema9 = features["ema9"]
    ema21 = features["ema21"]
    rsi = features["rsi14"]
    williams = features["williams_r14"]
    adx = features["adx14"]
    plus_di = features["plus_di"]
    minus_di = features["minus_di"]
    momentum = features["momentum"]

    if ema9 is not None and ema21 is not None:
        if ema9 > ema21:
            up_score += 3
            reasons.append("EMA9 فوق EMA21")
        elif ema9 < ema21:
            down_score += 3
            reasons.append("EMA9 تحت EMA21")

    if momentum > 0:
        up_score += 2
    elif momentum < 0:
        down_score += 2

    if rsi is not None:
        if 50 < rsi < 70:
            up_score += 1
        elif 30 < rsi < 50:
            down_score += 1

    if williams is not None:
        if williams > -50:
            up_score += 1
        elif williams < -50:
            down_score += 1

    if plus_di is not None and minus_di is not None:
        if plus_di > minus_di:
            up_score += 2
        elif minus_di > plus_di:
            down_score += 2

    if features["bullish_breakout"]:
        up_score += 2
        reasons.append("اختراق صاعد")
    elif features["bearish_breakout"]:
        down_score += 2
        reasons.append("اختراق هابط")

    last = candles[-1]

    if last["close"] > last["open"]:
        up_score += 1
    elif last["close"] < last["open"]:
        down_score += 1

    if adx is not None and adx >= 20:
        if plus_di is not None and minus_di is not None:
            if plus_di > minus_di:
                up_score += 1
            elif minus_di > plus_di:
                down_score += 1

    if up_score == down_score:
        decision = "UP" if momentum >= 0 else "DOWN"
    else:
        decision = "UP" if up_score > down_score else "DOWN"

    winning_score = max(up_score, down_score)
    total_score = up_score + down_score

    confidence = 55 + int(
        25 * winning_score / max(total_score, 1)
    )

    confidence = max(
        MIN_CONFIDENCE,
        min(MAX_CONFIDENCE, confidence),
    )

    return {
        "decision": decision,
        "confidence": confidence,
        "up_score": min(18, up_score),
        "down_score": min(18, down_score),
        "reason": "، ".join(reasons[:3]) or "تحليل المؤشرات والسعر",
    }


def get_ai_signal(symbol, timeframe, candles, features):
    if not genai_client:
        return fallback_signal(candles, features)

    compact_candles = [
        {
            "open": round(c["open"], 8),
            "high": round(c["high"], 8),
            "low": round(c["low"], 8),
            "close": round(c["close"], 8),
        }
        for c in candles[-60:]
    ]

    prompt_data = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": compact_candles,
        "indicators": features,
    }

    prompt = f"""
You are a cautious short-term technical analysis assistant.
Analyze the supplied market data. Do not invent indicators or prices.
Use price action, market structure, EMA 9/21, RSI 14,
Williams %R 14, and ADX/+DI/-DI.

Return JSON only:
{{
  "decision": "UP or DOWN",
  "confidence": 55,
  "up_score": 0,
  "down_score": 0,
  "reason": "short reason"
}}

Rules:
- decision must be UP or DOWN.
- Scores must be integers from 0 to 18.
- Confidence must be an integer from 55 to 88.
- Do not claim certainty or guaranteed profit.
- Avoid confidence above 80 unless several independent factors agree.
- Treat weak, conflicting evidence cautiously.
- No support/resistance lines in the final card.
- Use only the supplied data.
- The intended trade duration is 60 seconds.
- If evidence is mixed, choose the side with slightly stronger evidence
  and keep confidence close to the lower end.

DATA:
{json.dumps(prompt_data, ensure_ascii=False)}
"""

    try:
        response = genai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )

        parsed = json.loads(response.text)

        decision = str(parsed.get("decision", "UP")).upper()

        if decision not in ("UP", "DOWN"):
            decision = "UP"

        confidence = int(
            clean_number(parsed.get("confidence"), 60)
        )

        confidence = max(
            MIN_CONFIDENCE,
            min(MAX_CONFIDENCE, confidence),
        )

        up_score = int(clean_number(parsed.get("up_score"), 0))
        down_score = int(clean_number(parsed.get("down_score"), 0))

        return {
            "decision": decision,
            "confidence": confidence,
            "up_score": max(0, min(18, up_score)),
            "down_score": max(0, min(18, down_score)),
            "reason": str(
                parsed.get("reason", "تحليل حركة السعر")
            )[:180],
        }

    except Exception:
        logger.exception("Gemini analysis failed; using local fallback")
        return fallback_signal(candles, features)


# ============================================================
# SIGNAL CARD
# ============================================================

def make_signal_card(
    symbol,
    timeframe,
    signal,
    current_price,
    entry_time,
    stop_level,
):
    decision = signal["decision"]

    if decision == "UP":
        arrow = "🟢"
        direction = "UP / CALL"
        cancel_text = "إلغاء إذا أغلقت شمعة تحت"
    else:
        arrow = "🔴"
        direction = "DOWN / PUT"
        cancel_text = "إلغاء إذا أغلقت شمعة فوق"

    return (
        "🎓 <b>ZinoProSignalAI</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{symbol} | {timeframe}</b>\n"
        "⏱️ مدة الصفقة: <b>1 دقيقة</b>\n\n"
        f"{arrow} <b>القرار: {direction}</b>\n"
        f"🔥 الثقة: <b>{signal['confidence']}%</b>\n"
        f"🟢 UP Score: <b>{signal['up_score']}/18</b>\n"
        f"🔴 DOWN Score: <b>{signal['down_score']}/18</b>\n\n"
        f"🕒 الدخول المقترح: <b>{entry_time}</b>\n"
        f"💰 السعر الحالي عند التحليل: <b>{format_price(current_price)}</b>\n"
        f"⛔ {cancel_text} <b>{format_price(stop_level)}</b>\n\n"
        f"🧠 السبب: {signal['reason']}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ تحليل احتمالي وليس ضمانًا للربح."
    )


# ============================================================
# TELEGRAM
# ============================================================

def is_owner(update: Update):
    user = update.effective_user

    return bool(
        user
        and OWNER_ID_INT
        and user.id == OWNER_ID_INT
    )


async def send_owner_message(text, parse_mode=None):
    if not telegram_app or not OWNER_ID_INT:
        logger.error("Cannot send Telegram message: bot/owner not configured")
        return

    await telegram_app.bot.send_message(
        chat_id=OWNER_ID_INT,
        text=text,
        parse_mode=parse_mode,
    )


def send_owner_threadsafe(text, parse_mode=None):
    loop = telegram_loop

    if loop is None or loop.is_closed():
        logger.error("Telegram event loop is not ready")
        return False

    future = asyncio.run_coroutine_threadsafe(
        send_owner_message(text, parse_mode),
        loop,
    )

    try:
        future.result(timeout=20)
        return True
    except Exception:
        logger.exception("Telegram delivery failed")
        return False


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    await update.effective_message.reply_text(
        "🎓 ZinoProSignalAI شغال.\n\n"
        "الأوامر:\n"
        "/stats - الإحصائيات\n"
        "/win - تسجيل ربح\n"
        "/loss - تسجيل خسارة\n"
        "/reset - تصفير الإحصائيات\n\n"
        "اربط MT5 بالـEA وأرسل بيانات الشموع والسعر الحي."
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    with state_lock:
        signals = stats["signals"]
        wins = stats["wins"]
        losses = stats["losses"]

    total_results = wins + losses
    win_rate = (
        wins * 100.0 / total_results
        if total_results
        else 0.0
    )

    await update.effective_message.reply_text(
        "📊 ZinoProSignalAI — الإحصائيات\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📡 الإشارات: {signals}\n"
        f"🟢 الأرباح المسجلة: {wins}\n"
        f"🔴 الخسائر المسجلة: {losses}\n"
        f"🎯 نسبة الفوز: {win_rate:.1f}%\n"
        f"🕒 الوقت: {now_text()}"
    )


async def cmd_win(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    with state_lock:
        stats["wins"] += 1

    await update.effective_message.reply_text(
        "🟢 تم تسجيل ربح.\nاستعمل /stats لمشاهدة الإحصائيات."
    )


async def cmd_loss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    with state_lock:
        stats["losses"] += 1

    await update.effective_message.reply_text(
        "🔴 تم تسجيل خسارة.\nاستعمل /stats لمشاهدة الإحصائيات."
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    with state_lock:
        stats["signals"] = 0
        stats["wins"] = 0
        stats["losses"] = 0

    await update.effective_message.reply_text(
        "♻️ تم تصفير الإحصائيات."
    )


# ============================================================
# MARKET DATA AND ACTIVE TRADE MONITOR
# ============================================================

def update_market(body):
    symbol = str(
        body.get("symbol", body.get("asset", "UNKNOWN"))
    ).upper().strip()

    timeframe = str(
        body.get("timeframe", body.get("period", "M1"))
    ).upper().strip()

    candles = normalize_candles(
        body.get("candles", body.get("rates", []))
    )

    current_price = clean_number(
        body.get(
            "current_price",
            body.get("price", body.get("bid")),
        )
    )

    if not symbol or symbol == "UNKNOWN":
        raise ValueError("Missing symbol")

    if len(candles) < 30:
        raise ValueError(
            f"Need at least 30 valid candles; received {len(candles)}"
        )

    candle_time = candles[-1]["time"]

    market = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": candles,
        "current_price": current_price,
        "candle_time": candle_time,
        "updated_at": time.time(),
    }

    key = f"{symbol}|{timeframe}"

    with state_lock:
        latest_market[key] = market

    return key, market


def monitor_active_trade():
    global active_trade

    with state_lock:
        trade = dict(active_trade) if active_trade else None

    if not trade:
        return

    key = f"{trade['symbol']}|{trade['timeframe']}"

    with state_lock:
        market = latest_market.get(key)

    if not market:
        return

    age = time.time() - market["updated_at"]

    if age > MARKET_DATA_MAX_AGE_SECONDS:
        return

    price = market.get("current_price")

    # Do not pretend a candle close is the live market price.
    if price is None:
        return

    stop_level = trade.get("stop_level")
    decision = trade["decision"]

    stop_hit = False

    if stop_level is not None:
        if decision == "UP" and price <= stop_level:
            stop_hit = True

        elif decision == "DOWN" and price >= stop_level:
            stop_hit = True

    if stop_hit:
        with state_lock:
            if (
                active_trade
                and active_trade["trade_id"] == trade["trade_id"]
            ):
                active_trade = None
            else:
                return

        send_owner_threadsafe(
            "⚠️ <b>تنبيه إلغاء الإشارة</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📊 {trade['symbol']} | {trade['timeframe']}\n"
            f"📍 السعر الحالي: {format_price(price)}\n"
            f"⛔ مستوى الإلغاء: {format_price(stop_level)}\n"
            "السعر وصل إلى مستوى الإلغاء المحدد في البطاقة.\n"
            "━━━━━━━━━━━━━━━━━━",
            parse_mode="HTML",
        )
        return

    elapsed = time.time() - trade["created_at"]

    if elapsed >= TRADE_DURATION_SECONDS:
        with state_lock:
            if (
                active_trade
                and active_trade["trade_id"] == trade["trade_id"]
            ):
                active_trade = None
            else:
                return

        send_owner_threadsafe(
            "⏱️ انتهت مدة الإشارة.\n"
            f"📊 {trade['symbol']} | {trade['timeframe']}\n"
            "سجّل النتيجة يدويًا باستعمال /win أو /loss."
        )


def stop_monitor_worker():
    while True:
        try:
            monitor_active_trade()
        except Exception:
            logger.exception("Active trade monitor error")

        time.sleep(STOP_MONITOR_SECONDS)


# ============================================================
# SIGNAL PROCESSING
# ============================================================

def build_entry_time(timeframe):
    seconds = timeframe_seconds(timeframe)
    now = now_local()

    # Suggest an entry after one timeframe interval.
    entry = now + timedelta(seconds=seconds)

    return entry.strftime("%H:%M:%S")


def process_market_signal(market_key, market):
    global last_signal_time, active_trade, last_signal_card

    symbol = market["symbol"]
    timeframe = market["timeframe"]
    candles = market["candles"]
    candle_time = market["candle_time"]

    if not candle_time:
        logger.warning("Candle timestamps missing for %s", market_key)
        return {"ok": False, "message": "Candle timestamps are required"}

    signal_id = f"{market_key}|{candle_time}"

    with state_lock:
        if signal_id in processed_signals:
            return {"ok": True, "message": "Candle already processed"}

        if active_trade is not None:
            return {"ok": True, "message": "An active signal is still being monitored"}

        elapsed = time.time() - last_signal_time

        if elapsed < SIGNAL_INTERVAL_SECONDS:
            return {
                "ok": True,
                "message": "Global signal cooldown",
                "retry_after": int(SIGNAL_INTERVAL_SECONDS - elapsed),
            }

    price = market.get("current_price")

    if price is None:
        logger.warning("No live current_price for %s", market_key)
        return {
            "ok": False,
            "message": "Send current_price with every market update",
        }

    if time.time() - market["updated_at"] > MARKET_DATA_MAX_AGE_SECONDS:
        return {"ok": False, "message": "Market data is stale"}

    features = market_features(candles)
    signal = get_ai_signal(symbol, timeframe, candles, features)

    if signal["decision"] == "UP":
        stop_level = candles[-1]["low"]
    else:
        stop_level = candles[-1]["high"]

    entry_time = build_entry_time(timeframe)

    card = make_signal_card(
        symbol=symbol,
        timeframe=timeframe,
        signal=signal,
        current_price=price,
        entry_time=entry_time,
        stop_level=stop_level,
    )

    # Reserve the candle before sending so duplicate requests cannot
    # create multiple signals for the same candle.
    with state_lock:
        if signal_id in processed_signals:
            return {"ok": True, "message": "Duplicate candle"}

        if active_trade is not None:
            return {"ok": True, "message": "Active signal exists"}

        elapsed = time.time() - last_signal_time

        if elapsed < SIGNAL_INTERVAL_SECONDS:
            return {"ok": True, "message": "Global signal cooldown"}

        processed_signals.add(signal_id)

    delivered = send_owner_threadsafe(card, parse_mode="HTML")

    if not delivered:
        # Allow a retry if Telegram delivery failed.
        with state_lock:
            processed_signals.discard(signal_id)

        return {"ok": False, "message": "Telegram delivery failed"}

    trade_id = f"{signal_id}|{int(time.time())}"

    with state_lock:
        last_signal_time = time.time()
        last_signal_card = card
        stats["signals"] += 1

        active_trade = {
            "trade_id": trade_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "decision": signal["decision"],
            "confidence": signal["confidence"],
            "entry_price": price,
            "stop_level": stop_level,
            "created_at": time.time(),
            "signal_id": signal_id,
        }

    logger.info(
        "Signal delivered: %s %s %s confidence=%s",
        symbol,
        timeframe,
        signal["decision"],
        signal["confidence"],
    )

    return {
        "ok": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "decision": signal["decision"],
        "confidence": signal["confidence"],
        "telegram_delivered": True,
    }


def signal_worker(market_key, market):
    # Prevent concurrent AI analysis from multiple HTTP requests.
    if not analysis_lock.acquire(blocking=False):
        return {
            "ok": True,
            "message": "Analysis already in progress",
        }

    try:
        return process_market_signal(market_key, market)
    except Exception:
        logger.exception("Signal processing failed")
        return {"ok": False, "message": "Signal processing failed"}
    finally:
        analysis_lock.release()


# ============================================================
# HTTP API FOR MT4 / MT5
# ============================================================

class RequestHandler(BaseHTTPRequestHandler):

    def log_message(self, format_string, *args):
        logger.info("HTTP | " + format_string, *args)

    def send_json(self, status_code, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path in ("/", "/health", "/healthz"):
            self.send_json(200, {
                "ok": True,
                "service": "ZinoProSignalAI",
                "time": now_text(),
            })
            return

        self.send_json(404, {"ok": False, "message": "Not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path not in ("/mt4", "/mt5"):
            self.send_json(404, {"ok": False, "message": "Unknown endpoint"})
            return

        if not MT4_API_KEY:
            self.send_json(
                503,
                {"ok": False, "message": "MT4_API_KEY is not configured"},
            )
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))

            if content_length <= 0 or content_length > 2_000_000:
                self.send_json(400, {
                    "ok": False,
                    "message": "Invalid request body size",
                })
                return

            raw = self.rfile.read(content_length)
            body = json.loads(raw.decode("utf-8"))

            if not isinstance(body, dict):
                self.send_json(400, {
                    "ok": False,
                    "message": "JSON object required",
                })
                return

            header_key = self.headers.get("X-API-Key", "").strip()
            body_key = str(body.get("api_key", "")).strip()

            if header_key != MT4_API_KEY and body_key != MT4_API_KEY:
                self.send_json(401, {
                    "ok": False,
                    "message": "Unauthorized",
                })
                return

            market_key, market = update_market(body)

            # Every request updates the live price first. Repeated
            # requests can refresh price without creating repeated signals.
            result = signal_worker(market_key, market)

            self.send_json(200 if result.get("ok") else 400, result)

        except ValueError as exc:
            self.send_json(400, {
                "ok": False,
                "message": str(exc),
            })

        except Exception:
            logger.exception("HTTP request failed")

            try:
                self.send_json(500, {
                    "ok": False,
                    "message": "Internal server error",
                })
            except Exception:
                pass


def start_http_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RequestHandler)
    server.daemon_threads = True

    logger.info("HTTP server listening on port %s", PORT)

    server.serve_forever()


# ============================================================
# APPLICATION LIFECYCLE
# ============================================================

async def post_init(application: Application):
    global telegram_app, telegram_loop, genai_client

    telegram_app = application
    telegram_loop = asyncio.get_running_loop()

    if GEMINI_API_KEY and genai:
        try:
            genai_client = genai.Client(api_key=GEMINI_API_KEY)
            logger.info("Gemini client initialized")
        except Exception:
            genai_client = None
            logger.exception("Could not initialize Gemini")

    elif GEMINI_API_KEY and not genai:
        logger.error(
            "google-genai package is missing; using local fallback analysis"
        )

    threading.Thread(
        target=start_http_server,
        name="http-server",
        daemon=True,
    ).start()

    threading.Thread(
        target=stop_monitor_worker,
        name="trade-monitor",
        daemon=True,
    ).start()

    logger.info("ZinoProSignalAI started at %s", now_text())


async def post_shutdown(application: Application):
    logger.info("ZinoProSignalAI shutting down")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN environment variable")

    if not OWNER_ID_INT:
        raise RuntimeError("Missing or invalid OWNER_ID environment variable")

    if not MT4_API_KEY:
        raise RuntimeError("Missing MT4_API_KEY environment variable")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("win", cmd_win))
    application.add_handler(CommandHandler("loss", cmd_loss))
    application.add_handler(CommandHandler("reset", cmd_reset))

    logger.info("Starting Telegram polling")

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
