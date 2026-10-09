import os
import json
import math
import logging
import threading
import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from google import genai
from google.genai import types

# ============================================================
# ZinoProSignalAI - MT5/MT4 + Render + Telegram
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = os.getenv("OWNER_ID", "").strip()
MT4_API_KEY = os.getenv("MT4_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
PORT = int(os.getenv("PORT", "10000"))

ALGIERS = ZoneInfo("Africa/Algiers")

try:
    OWNER_ID = int(OWNER_ID)
except (TypeError, ValueError):
    OWNER_ID = 0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("ZinoProSignalAI")

telegram_app = None
telegram_loop = None
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

stats_lock = threading.Lock()
stats = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
    "last_signal": None,
}

# Prevent duplicate analysis/delivery for the same closed candle.
signal_lock = threading.Lock()
signals_in_progress = set()
processed_signals = {}
MAX_SIGNAL_CACHE = 1000


# ============================================================
# UTILITIES
# ============================================================

def now_algiers():
    return datetime.now(ALGIERS)


def number(value, default=None):
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return default


def timeframe_seconds(timeframe):
    """Convert MT4/MT5 timeframe strings to seconds."""
    tf = str(timeframe).strip().upper()

    if tf.startswith("MN"):
        return 30 * 24 * 60 * 60

    match = re.fullmatch(r"(M|H|D|W)(\d+)", tf)
    if not match:
        return 60

    unit, amount = match.groups()
    amount = int(amount)

    if amount <= 0:
        return 60

    multipliers = {
        "M": 60,
        "H": 3600,
        "D": 86400,
        "W": 604800,
    }

    return amount * multipliers[unit]


def normalize_candles(raw):
    if not isinstance(raw, list):
        raise ValueError("candles must be a JSON array")

    by_time = {}

    for item in raw[-200:]:
        if not isinstance(item, dict):
            continue

        o = number(item.get("open"))
        h = number(item.get("high"))
        l = number(item.get("low"))
        c = number(item.get("close"))
        t = number(item.get("time"))

        if None in (o, h, l, c, t):
            continue

        if t <= 0:
            continue

        if h < l or h < max(o, c) or l > min(o, c):
            continue

        timestamp = int(t)

        by_time[timestamp] = {
            "time": timestamp,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        }

    candles = [by_time[t] for t in sorted(by_time)]

    if len(candles) < 30:
        raise ValueError(
            f"At least 30 valid candles are required; got {len(candles)}"
        )

    return candles[-200:]


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None

    result = sum(values[:period]) / period
    alpha = 2.0 / (period + 1)

    for value in values[period:]:
        result = alpha * value + (1 - alpha) * result

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

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
    return 100 - 100 / (1 + rs)


def williams_r(candles, period=14):
    if len(candles) < period:
        return None

    sample = candles[-period:]
    highest = max(c["high"] for c in sample)
    lowest = min(c["low"] for c in sample)

    if highest == lowest:
        return -50.0

    close = candles[-1]["close"]
    return -100 * (highest - close) / (highest - lowest)


def adx_di(candles, period=14):
    if len(candles) < period + 2:
        return None, None, None

    trs = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(candles)):
        cur = candles[i]
        prev = candles[i - 1]

        up = cur["high"] - prev["high"]
        down = prev["low"] - cur["low"]

        plus_dm.append(
            up if up > down and up > 0 else 0.0
        )
        minus_dm.append(
            down if down > up and down > 0 else 0.0
        )

        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))

    tr = sum(trs[:period])
    pdm = sum(plus_dm[:period])
    mdm = sum(minus_dm[:period])

    dx_values = []
    plus_di = None
    minus_di = None

    for i in range(period, len(trs)):
        tr = tr - tr / period + trs[i]
        pdm = pdm - pdm / period + plus_dm[i]
        mdm = mdm - mdm / period + minus_dm[i]

        if tr <= 0:
            continue

        plus_di = 100 * pdm / tr
        minus_di = 100 * mdm / tr
        total = plus_di + minus_di

        dx_values.append(
            100 * abs(plus_di - minus_di) / total
            if total else 0
        )

    adx_value = None

    if dx_values:
        recent_dx = dx_values[-period:]
        adx_value = sum(recent_dx) / len(recent_dx)

    return adx_value, plus_di, minus_di


# ============================================================
# MARKET FEATURES
# ============================================================

def market_features(candles):
    closes = [c["close"] for c in candles]
    last = candles[-1]
    previous = candles[-2]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    r = rsi(closes, 14)
    wr = williams_r(candles, 14)
    adx, plus_di, minus_di = adx_di(candles, 14)

    recent = candles[-21:-1]
    recent_high = max(c["high"] for c in recent)
    recent_low = min(c["low"] for c in recent)

    return {
        "close": last["close"],
        "open": last["open"],
        "high": last["high"],
        "low": last["low"],
        "previous_close": previous["close"],
        "ema9": e9,
        "ema21": e21,
        "rsi14": r,
        "williams_r14": wr,
        "adx14": adx,
        "plus_di14": plus_di,
        "minus_di14": minus_di,
        "breakout_up": last["close"] > recent_high,
        "breakout_down": last["close"] < recent_low,
        "recent_high": recent_high,
        "recent_low": recent_low,
    }


# ============================================================
# FALLBACK ANALYSIS
# ============================================================

def fallback_signal(f):
    up = 0
    down = 0

    if f["ema9"] is not None and f["ema21"] is not None:
        if f["ema9"] > f["ema21"]:
            up += 2
        elif f["ema9"] < f["ema21"]:
            down += 2

    if f["close"] > f["open"]:
        up += 1
    elif f["close"] < f["open"]:
        down += 1

    r = f["rsi14"]

    if r is not None:
        if 50 < r < 68:
            up += 1
        elif 32 < r < 50:
            down += 1

    wr = f["williams_r14"]

    if wr is not None and -80 < wr < -20:
        if f["close"] > f["previous_close"]:
            up += 1
        elif f["close"] < f["previous_close"]:
            down += 1

    pdi = f["plus_di14"]
    mdi = f["minus_di14"]

    if pdi is not None and mdi is not None:
        if pdi > mdi:
            up += 1
        elif mdi > pdi:
            down += 1

    if f["breakout_up"]:
        up += 2

    if f["breakout_down"]:
        down += 2

    if f["adx14"] is not None and f["adx14"] < 15:
        up = max(0, up - 1)
        down = max(0, down - 1)

    decision = "UP" if up >= down else "DOWN"
    confidence = min(65, 50 + abs(up - down) * 3)

    # The fallback uses fewer than 18 independent points.
    # Scale the raw score to the displayed 18-point range.
    up_score = min(18, round(up * 18 / 9))
    down_score = min(18, round(down * 18 / 9))

    return {
        "decision": decision,
        "confidence": confidence,
        "up_score": up_score,
        "down_score": down_score,
        "reason": (
            "تحليل احتياطي مبني على المؤشرات المتاحة؛ "
            "تعذر الحصول على تحليل Gemini."
        ),
    }


# ============================================================
# GEMINI ANALYSIS
# ============================================================

def get_ai_signal(symbol, timeframe, candles, features):
    if gemini_client is None:
        return fallback_signal(features)

    prompt = f"""
أنت محلل فني حذر لبيانات شموع MT5 لإشارات قصيرة الأجل.
استخدم فقط بيانات OHLC والمؤشرات المحسوبة المرفقة.
لا تخترع أي قيم أو مؤشرات أو أحجام تداول غير موجودة.

الإطار الزمني: {timeframe}
مدة الصفقة المقصودة: دقيقة واحدة.
المؤشرات: EMA 9/21, RSI 14, Williams %R 14, ADX 14, +DI, -DI.

رتب الأدلة هكذا:
حركة السعر وبنية السوق، الاختراق، الزخم، الشموع،
EMA، RSI، Williams %R، ثم ADX/DI.

يجب اختيار UP أو DOWN.
إذا تعارضت الأدلة، اجعل الثقة منخفضة ولا تبالغ في التقييم.
الثقة لا تتجاوز 80.
النقاط أعداد صحيحة من 0 إلى 18، ويجب أن تعكس الأدلة.
اكتب السبب بالعربية، ولا تدّعِ ضمان الربح.
لا ترسل مستوى إلغاء؛ سيُحسب من الشمعة المغلقة في البرنامج.

أعد JSON فقط بهذه الحقول:
decision, confidence, up_score, down_score, reason

Symbol: {symbol}
Features: {json.dumps(features, separators=(',', ':'))}
Last 40 closed candles:
{json.dumps(candles[-40:], separators=(',', ':'))}
"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )

        result = json.loads(response.text or "{}")
        decision = str(result.get("decision", "")).upper()

        if decision not in ("UP", "DOWN"):
            raise ValueError("Invalid AI decision")

        confidence = int(number(result.get("confidence"), 55))
        up_score = int(number(result.get("up_score"), 0))
        down_score = int(number(result.get("down_score"), 0))

        return {
            "decision": decision,
            "confidence": max(50, min(80, confidence)),
            "up_score": max(0, min(18, up_score)),
            "down_score": max(0, min(18, down_score)),
            "reason": str(
                result.get("reason", "تحليل فني")
            )[:250],
        }

    except Exception:
        log.exception("Gemini failed; using fallback analysis")
        return fallback_signal(features)


# ============================================================
# SIGNAL TIME AND MESSAGE
# ============================================================

def suggested_entry_time(last_candle, timeframe):
    """
    Candle timestamps are opening times.
    The next candle boundary is the first candidate.
    If that time has already passed, use the next future boundary.
    """
    seconds = timeframe_seconds(timeframe)
    candidate = int(last_candle["time"]) + seconds
    now_ts = int(now_algiers().timestamp())

    if candidate <= now_ts:
        candidate = (now_ts // seconds + 1) * seconds

    return datetime.fromtimestamp(candidate, ALGIERS)


def price_digits(symbol):
    symbol = symbol.upper()

    if "JPY" in symbol:
        return 3

    if any(x in symbol for x in ("XAU", "XAG")):
        return 2

    return 5


def format_signal(symbol, timeframe, result, f, last_candle):
    entry = suggested_entry_time(last_candle, timeframe)
    digits = price_digits(symbol)

    price = f["close"]
    decision = result["decision"]
    emoji = "🟢" if decision == "UP" else "🔴"

    # Cancellation level is deterministic and based on the last
    # closed candle, not generated by the language model.
    if decision == "UP":
        cancellation = (
            f"إلغاء الفكرة إذا أغلقت شمعة تحت "
            f"{f['low']:.{digits}f}"
        )
    else:
        cancellation = (
            f"إلغاء الفكرة إذا أغلقت شمعة فوق "
            f"{f['high']:.{digits}f}"
        )

    return (
        "🎓 <b>ZinoProSignalAI</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{symbol} | {timeframe}</b>\n"
        "⏳ مدة الصفقة: <b>1 minute</b>\n\n"
        f"{emoji} القرار: <b>{decision}</b>\n"
        f"🔥 الثقة التقديرية: <b>{result['confidence']}%</b>\n"
        f"🟢 UP Score: <b>{result['up_score']}/18</b>\n"
        f"🔴 DOWN Score: <b>{result['down_score']}/18</b>\n\n"
        f"💰 آخر إغلاق: <code>{price:.{digits}f}</code>\n"
        f"⏰ وقت الدخول المقترح: "
        f"<b>{entry.strftime('%H:%M:%S')} الجزائر</b>\n"
        f"🛑 {cancellation}\n\n"
        f"📝 السبب: {result.get('reason', 'تحليل فني')}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ تحليل احتمالي وليس ضمانًا للربح."
    )


# ============================================================
# TELEGRAM DELIVERY
# ============================================================

async def send_owner(message):
    if telegram_app is None or not OWNER_ID:
        raise RuntimeError(
            "Telegram bot or OWNER_ID is not configured"
        )

    await telegram_app.bot.send_message(
        chat_id=OWNER_ID,
        text=message,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def send_owner_threadsafe(message):
    if telegram_loop is None or telegram_loop.is_closed():
        log.error("Telegram event loop unavailable")
        return False

    future = asyncio.run_coroutine_threadsafe(
        send_owner(message), telegram_loop
    )

    try:
        future.result(timeout=20)
        return True
    except Exception:
        log.exception("Telegram message delivery failed")
        return False


# ============================================================
# HTTP API FOR MT4 / MT5
# ============================================================

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        log.info("HTTP: " + fmt, *args)

    def reply(self, status, data):
        raw = json.dumps(
            data, ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            return self.reply(200, {
                "ok": True,
                "service": "ZinoProSignalAI",
            })

        return self.reply(404, {
            "ok": False,
            "error": "Not found",
        })

    def do_POST(self):
        endpoint = self.path.split("?", 1)[0].rstrip("/")

        if endpoint not in ("/mt4", "/mt5"):
            return self.reply(404, {
                "ok": False,
                "error": "Unknown endpoint",
            })

        if not MT4_API_KEY:
            return self.reply(503, {
                "ok": False,
                "error": "MT4_API_KEY missing on Render",
            })

        try:
            length = int(
                self.headers.get("Content-Length", "0")
            )
        except ValueError:
            length = 0

        if length <= 0 or length > 2_000_000:
            return self.reply(400, {
                "ok": False,
                "error": "Invalid body size",
            })

        try:
            payload = json.loads(
                self.rfile.read(length).decode("utf-8")
            )
        except Exception:
            return self.reply(400, {
                "ok": False,
                "error": "Invalid JSON",
            })

        if not isinstance(payload, dict):
            return self.reply(400, {
                "ok": False,
                "error": "JSON object required",
            })

        header_key = self.headers.get("X-API-Key", "")
        body_key = str(payload.get("api_key", ""))

        if (
            header_key != MT4_API_KEY
            and body_key != MT4_API_KEY
        ):
            return self.reply(401, {
                "ok": False,
                "error": "API key mismatch",
            })

        symbol = str(
            payload.get("symbol", "")
        ).strip().upper()

        timeframe = str(
            payload.get("timeframe", "M1")
        ).strip().upper()

        if not symbol or len(symbol) > 40:
            return self.reply(400, {
                "ok": False,
                "error": "Invalid symbol",
            })

        try:
            candles = normalize_candles(
                payload.get("candles")
            )

            last_candle = candles[-1]
            candle_time = last_candle["time"]

            # Include candle timestamp in the key. Repeated requests
            # for the same symbol/timeframe/candle must not send again.
            signal_key = (
                f"{symbol}|{timeframe}|{candle_time}"
            )

            with signal_lock:
                cached = processed_signals.get(signal_key)

                if cached is not None:
                    return self.reply(200, {
                        "ok": True,
                        "duplicate": True,
                        **cached,
                    })

                if signal_key in signals_in_progress:
                    return self.reply(200, {
                        "ok": True,
                        "duplicate": True,
                        "processing": True,
                        "symbol": symbol,
                        "timeframe": timeframe,
                    })

                signals_in_progress.add(signal_key)

            try:
                features = market_features(candles)

                result = get_ai_signal(
                    symbol,
                    timeframe,
                    candles,
                    features,
                )

                message = format_signal(
                    symbol,
                    timeframe,
                    result,
                    features,
                    last_candle,
                )

                delivered = send_owner_threadsafe(message)

                if not delivered:
                    return self.reply(502, {
                        "ok": False,
                        "error": "Telegram delivery failed",
                        "symbol": symbol,
                        "timeframe": timeframe,
                    })

                response_data = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "decision": result["decision"],
                    "confidence": result["confidence"],
                    "telegram_delivered": True,
                }

                with stats_lock:
                    stats["signals"] += 1
                    stats["last_signal"] = {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "decision": result["decision"],
                        "confidence": result["confidence"],
                        "candle_time": candle_time,
                        "time": now_algiers().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    }

                with signal_lock:
                    processed_signals[signal_key] = response_data

                    # Keep memory usage bounded.
                    while len(processed_signals) > MAX_SIGNAL_CACHE:
                        oldest_key = next(iter(processed_signals))
                        processed_signals.pop(oldest_key, None)

                return self.reply(200, {
                    "ok": True,
                    **response_data,
                })

            finally:
                with signal_lock:
                    signals_in_progress.discard(signal_key)

        except ValueError as exc:
            return self.reply(400, {
                "ok": False,
                "error": str(exc),
            })

        except Exception:
            log.exception("MT4/MT5 request failed")
            return self.reply(500, {
                "ok": False,
                "error": "Internal server error",
            })


def run_http_server():
    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        Handler,
    )
    log.info("HTTP server listening on port %s", PORT)
    server.serve_forever()


# ============================================================
# OWNER COMMANDS
# ============================================================

def is_owner(update):
    return bool(
        update.effective_user
        and update.effective_user.id == OWNER_ID
    )


async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_owner(update):
        return

    await update.effective_message.reply_text(
        "🎓 ZinoProSignalAI يعمل.\n\n"
        "MT4 endpoint: /mt4\n"
        "MT5 endpoint: /mt5\n"
        "/stats - الإحصائيات\n"
        "/win - تسجيل ربح\n"
        "/loss - تسجيل خسارة\n"
        "/reset - تصفير الإحصائيات"
    )


async def cmd_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_owner(update):
        return

    with stats_lock:
        s = dict(stats)

    total = s["wins"] + s["losses"]
    rate = 100 * s["wins"] / total if total else 0
    last = s["last_signal"]

    last_text = "لا توجد إشارة بعد"

    if last:
        last_text = (
            f"{last['symbol']} {last['timeframe']} | "
            f"{last['decision']} | {last['confidence']}% | "
            f"{last['time']}"
        )

    await update.effective_message.reply_text(
        "📊 ZinoProSignalAI STATS\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📨 الإشارات المرسلة: {s['signals']}\n"
        f"🟢 الأرباح المسجلة: {s['wins']}\n"
        f"🔴 الخسائر المسجلة: {s['losses']}\n"
        f"🎯 نسبة الفوز المسجلة يدويًا: {rate:.1f}%\n"
        f"🕒 آخر إشارة: {last_text}"
    )


async def cmd_win(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_owner(update):
        return

    with stats_lock:
        stats["wins"] += 1

    await update.effective_message.reply_text(
        "🟢 تم تسجيل ربح."
    )


async def cmd_loss(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_owner(update):
        return

    with stats_lock:
        stats["losses"] += 1

    await update.effective_message.reply_text(
        "🔴 تم تسجيل خسارة."
    )


async def cmd_reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_owner(update):
        return

    with stats_lock:
        stats.update({
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "last_signal": None,
        })

    await update.effective_message.reply_text(
        "♻️ تم تصفير الإحصائيات."
    )


async def post_init(application):
    global telegram_loop
    telegram_loop = asyncio.get_running_loop()
    log.info("Telegram initialized")


# ============================================================
# START
# ============================================================

def main():
    global telegram_app

    if not BOT_TOKEN:
        raise RuntimeError(
            "Set BOT_TOKEN in Render environment variables"
        )

    if not OWNER_ID:
        raise RuntimeError(
            "Set OWNER_ID to your numeric Telegram user ID"
        )

    if not MT4_API_KEY:
        log.warning("MT4_API_KEY is missing")

    if not GEMINI_API_KEY:
        log.warning(
            "GEMINI_API_KEY is missing; fallback analysis enabled"
        )

    threading.Thread(
        target=run_http_server,
        daemon=True,
        name="http-server",
    ).start()

    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler("start", cmd_start)
    )
    telegram_app.add_handler(
        CommandHandler("stats", cmd_stats)
    )
    telegram_app.add_handler(
        CommandHandler("win", cmd_win)
    )
    telegram_app.add_handler(
        CommandHandler("loss", cmd_loss)
    )
    telegram_app.add_handler(
        CommandHandler("reset", cmd_reset)
    )

    log.info("Starting ZinoProSignalAI")

    telegram_app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
