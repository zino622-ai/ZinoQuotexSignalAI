import os
import json
import math
import logging
import threading
import asyncio
import re
import time
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from google import genai
from google.genai import types

# ============================================================
# ZinoProSignalAI - MT4/MT5 + Render + Telegram
# Signal cooldown: 180 seconds | Trade duration: 60 seconds
# Stop monitor: every 30 seconds | Timezone: Algeria
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = os.getenv("OWNER_ID", "").strip()
MT4_API_KEY = os.getenv("MT4_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
PORT = int(os.getenv("PORT", "10000"))
SIGNAL_INTERVAL_SECONDS = 180
TRADE_DURATION_SECONDS = 60
STOP_MONITOR_SECONDS = 30
MARKET_DATA_MAX_AGE_SECONDS = 90
ALGIERS = ZoneInfo("Africa/Algiers")

try:
    OWNER_ID = int(OWNER_ID)
except (TypeError, ValueError):
    OWNER_ID = 0

# Logs go to Render console and rotating bot.log.
_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
_root = logging.getLogger()
_root.setLevel(logging.INFO)
_console = logging.StreamHandler()
_console.setFormatter(_formatter)
_root.addHandler(_console)
try:
    _file = RotatingFileHandler("bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    _file.setFormatter(_formatter)
    _root.addHandler(_file)
except OSError:
    _root.exception("Could not open bot.log; console logging remains enabled")
log = logging.getLogger("ZinoProSignalAI")

telegram_app = None
telegram_loop = None
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

stats_lock = threading.Lock()
stats = {"signals": 0, "wins": 0, "losses": 0, "last_signal": None}

state_lock = threading.RLock()
signal_lock = threading.Lock()
generation_lock = threading.Lock()  # serializes signal generation across all symbols
signals_in_progress = set()
processed_signals = {}
MAX_SIGNAL_CACHE = 1000
last_signal_epoch = 0.0
active_trade = None
latest_market = {}  # keyed by symbol|timeframe; refreshed by each valid EA request


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
    return amount * {"M": 60, "H": 3600, "D": 86400, "W": 604800}[unit]


def normalize_candles(raw):
    if not isinstance(raw, list):
        raise ValueError("candles must be a JSON array")
    by_time = {}
    for item in raw[-200:]:
        if not isinstance(item, dict):
            continue
        o, h, l, c, t = (number(item.get(k)) for k in ("open", "high", "low", "close", "time"))
        if None in (o, h, l, c, t) or t <= 0:
            continue
        if h < l or h < max(o, c) or l > min(o, c):
            continue
        timestamp = int(t)
        by_time[timestamp] = {"time": timestamp, "open": o, "high": h, "low": l, "close": c}
    candles = [by_time[t] for t in sorted(by_time)]
    if len(candles) < 30:
        raise ValueError(f"At least 30 valid candles are required; got {len(candles)}")
    return candles[-200:]

# ---------------------------- Indicators ----------------------------
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
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
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
    return -100 * (highest - candles[-1]["close"]) / (highest - lowest)


def adx_di(candles, period=14):
    if len(candles) < period + 2:
        return None, None, None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        up, down = cur["high"] - prev["high"], prev["low"] - cur["low"]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"])))
    tr, pdm, mdm = sum(trs[:period]), sum(plus_dm[:period]), sum(minus_dm[:period])
    dx_values, plus_di, minus_di = [], None, None
    for i in range(period, len(trs)):
        tr = tr - tr / period + trs[i]
        pdm = pdm - pdm / period + plus_dm[i]
        mdm = mdm - mdm / period + minus_dm[i]
        if tr <= 0:
            continue
        plus_di, minus_di = 100 * pdm / tr, 100 * mdm / tr
        total = plus_di + minus_di
        dx_values.append(100 * abs(plus_di - minus_di) / total if total else 0)
    adx_value = None
    if dx_values:
        recent = dx_values[-period:]
        adx_value = sum(recent) / len(recent)
    return adx_value, plus_di, minus_di


def market_features(candles):
    closes = [c["close"] for c in candles]
    last, previous = candles[-1], candles[-2]
    e9, e21 = ema(closes, 9), ema(closes, 21)
    recent = candles[-21:-1]
    recent_high = max(c["high"] for c in recent)
    recent_low = min(c["low"] for c in recent)
    adx, plus_di, minus_di = adx_di(candles, 14)
    return {
        "close": last["close"], "open": last["open"], "high": last["high"], "low": last["low"],
        "previous_close": previous["close"], "ema9": e9, "ema21": e21,
        "rsi14": rsi(closes, 14), "williams_r14": williams_r(candles, 14),
        "adx14": adx, "plus_di14": plus_di, "minus_di14": minus_di,
        "breakout_up": last["close"] > recent_high, "breakout_down": last["close"] < recent_low,
        "recent_high": recent_high, "recent_low": recent_low,
    }

# ---------------------------- Signal analysis ----------------------------
def fallback_signal(f):
    up = down = 0
    if f["ema9"] is not None and f["ema21"] is not None:
        if f["ema9"] > f["ema21"]: up += 2
        elif f["ema9"] < f["ema21"]: down += 2
    if f["close"] > f["open"]: up += 1
    elif f["close"] < f["open"]: down += 1
    r = f["rsi14"]
    if r is not None:
        if 50 < r < 68: up += 1
        elif 32 < r < 50: down += 1
    wr = f["williams_r14"]
    if wr is not None and -80 < wr < -20:
        if f["close"] > f["previous_close"]: up += 1
        elif f["close"] < f["previous_close"]: down += 1
    pdi, mdi = f["plus_di14"], f["minus_di14"]
    if pdi is not None and mdi is not None:
        if pdi > mdi: up += 1
        elif mdi > pdi: down += 1
    if f["breakout_up"]: up += 2
    if f["breakout_down"]: down += 2
    if f["adx14"] is not None and f["adx14"] < 15:
        up, down = max(0, up - 1), max(0, down - 1)
    decision = "UP" if up >= down else "DOWN"
    return {
        "decision": decision,
        "confidence": min(65, 50 + abs(up - down) * 3),
        "up_score": min(18, round(up * 18 / 9)),
        "down_score": min(18, round(down * 18 / 9)),
        "reason": "تحليل احتياطي مبني على المؤشرات المتاحة؛ تعذر الحصول على تحليل Gemini.",
    }


def get_ai_signal(symbol, timeframe, candles, features):
    if gemini_client is None:
        return fallback_signal(features)
    prompt = f"""
أنت محلل فني حذر لبيانات شموع MT4/MT5 لإشارات قصيرة الأجل.
استخدم فقط بيانات OHLC والمؤشرات المرفقة ولا تخترع مؤشرات.
الإطار الزمني: {timeframe}. مدة الصفقة المقصودة: دقيقة واحدة.
المؤشرات: EMA 9/21, RSI 14, Williams %R 14, ADX 14, +DI, -DI.
رتب الأدلة: حركة السعر وبنية السوق، الاختراق، الزخم، الشموع، EMA، RSI، Williams %R، ADX/DI.
اختر UP أو DOWN، وخفّض الثقة إذا تعارضت الأدلة. الثقة لا تتجاوز 80.
النقاط أعداد صحيحة من 0 إلى 18. السبب بالعربية. لا تدّعِ ضمان الربح.
أعد JSON فقط: decision, confidence, up_score, down_score, reason
Symbol: {symbol}
Features: {json.dumps(features, separators=(',', ':'))}
Last 40 candles: {json.dumps(candles[-40:], separators=(',', ':'))}
"""
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
        )
        result = json.loads(response.text or "{}")
        decision = str(result.get("decision", "")).upper()
        if decision not in ("UP", "DOWN"):
            raise ValueError("Invalid AI decision")
        return {
            "decision": decision,
            "confidence": max(50, min(80, int(number(result.get("confidence"), 55)))),
            "up_score": max(0, min(18, int(number(result.get("up_score"), 0)))),
            "down_score": max(0, min(18, int(number(result.get("down_score"), 0)))),
            "reason": str(result.get("reason", "تحليل فني"))[:250],
        }
    except Exception:
        log.exception("Gemini failed; using fallback analysis")
        return fallback_signal(features)

# ---------------------------- Time / card ----------------------------
def suggested_entry_time(last_candle, timeframe):
    """Choose a near-future boundary; log suspicious MT4/MT5 timestamps."""
    seconds = timeframe_seconds(timeframe)
    now = now_algiers()
    now_ts = now.timestamp()
    candle_ts = int(last_candle["time"])
    candidate = candle_ts + seconds
    if candidate > now_ts + max(seconds * 2, 30):
        log.warning("Future candle timestamp detected: candle=%s now=%s timeframe=%s", datetime.fromtimestamp(candle_ts, ALGIERS).isoformat(), now.isoformat(), timeframe)
        candidate = 0
    if candidate < now_ts + 5:
        candidate = (math.floor((now_ts + 5) / seconds) + 1) * seconds
    entry = datetime.fromtimestamp(candidate, ALGIERS)
    log.info("ENTRY TIME | now_algiers=%s | entry_algiers=%s | timeframe=%s | candle_timestamp=%s", now.strftime("%Y-%m-%d %H:%M:%S"), entry.strftime("%Y-%m-%d %H:%M:%S"), timeframe, candle_ts)
    return entry


def price_digits(symbol):
    symbol = symbol.upper()
    if "JPY" in symbol: return 3
    if any(x in symbol for x in ("XAU", "XAG")): return 2
    return 5


def format_signal(symbol, timeframe, result, f, last_candle, entry):
    digits = price_digits(symbol)
    price, decision = f["close"], result["decision"]
    emoji = "🟢" if decision == "UP" else "🔴"
    cancellation = (f"إلغاء الفكرة إذا أغلقت شمعة تحت {f['low']:.{digits}f}" if decision == "UP" else f"إلغاء الفكرة إذا أغلقت شمعة فوق {f['high']:.{digits}f}")
    return (
        "🎓 <b>ZinoProSignalAI</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{symbol} | {timeframe}</b>\n⏳ مدة الصفقة: <b>1 minute</b>\n\n"
        f"{emoji} القرار: <b>{decision}</b>\n🔥 الثقة التقديرية: <b>{result['confidence']}%</b>\n"
        f"🟢 UP Score: <b>{result['up_score']}/18</b>\n🔴 DOWN Score: <b>{result['down_score']}/18</b>\n\n"
        f"💰 آخر إغلاق: <code>{price:.{digits}f}</code>\n"
        f"⏰ وقت الدخول المقترح: <b>{entry.strftime('%H:%M:%S')} الجزائر</b>\n"
        f"🛑 {cancellation}\n\n📝 السبب: {result.get('reason', 'تحليل فني')}\n"
        "━━━━━━━━━━━━━━━━━━\n⚠️ تحليل احتمالي وليس ضمانًا للربح."
    )

# ---------------------------- Telegram delivery ----------------------------
async def send_owner(message):
    if telegram_app is None or not OWNER_ID:
        raise RuntimeError("Telegram bot or OWNER_ID is not configured")
    await telegram_app.bot.send_message(chat_id=OWNER_ID, text=message, parse_mode="HTML", disable_web_page_preview=True)


def send_owner_threadsafe(message):
    if telegram_loop is None or telegram_loop.is_closed():
        log.error("Telegram event loop unavailable")
        return False
    future = asyncio.run_coroutine_threadsafe(send_owner(message), telegram_loop)
    try:
        future.result(timeout=20)
        return True
    except Exception:
        log.exception("Telegram message delivery failed")
        return False

# ---------------------------- Active trade / stop monitor ----------------------------
def close_active_trade(reason, observed_price=None):
    global active_trade
    with state_lock:
        trade = active_trade
        if not trade:
            return
        active_trade = None
    log.info("TRADE CLOSED | reason=%s | symbol=%s | decision=%s | observed_price=%s", reason, trade["symbol"], trade["decision"], observed_price)
    if reason == "STOP_LEVEL_REACHED":
        msg = (f"🛑 <b>مراقبة الستوب</b>\n{trade['symbol']} | {trade['timeframe']} | {trade['decision']}\n"
               f"تم تجاوز مستوى الإلغاء. السعر المرصود: <code>{observed_price}</code>\n"
               "هذا تنبيه مراقبة فقط وليس تنفيذًا لأمر تداول.")
        send_owner_threadsafe(msg)
    elif reason == "DURATION_EXPIRED":
        log.info("One-minute trade monitoring period ended for %s", trade["symbol"])


def stop_monitor_loop():
    """Check active signal every 30 seconds. Fresh price from EA is preferred."""
    while True:
        time.sleep(STOP_MONITOR_SECONDS)
        try:
            with state_lock:
                trade = dict(active_trade) if active_trade else None
                market = dict(latest_market.get(trade["market_key"], {})) if trade else {}
            if not trade:
                continue
            now = time.time()
            if now >= trade["expires_at"]:
                close_active_trade("DURATION_EXPIRED")
                continue
            data_age = now - market.get("received_at", 0)
            if data_age > MARKET_DATA_MAX_AGE_SECONDS:
                log.warning("STOP MONITOR | no fresh market data for %s (age=%.1fs); cannot verify stop", trade["symbol"], data_age)
                continue
            price = market.get("price")
            if price is None:
                log.warning("STOP MONITOR | EA has not sent current price for %s; using latest candle close", trade["symbol"])
                price = market.get("close")
            if price is None:
                continue
            hit = (price <= trade["stop_level"]) if trade["decision"] == "UP" else (price >= trade["stop_level"])
            log.info("STOP CHECK | symbol=%s decision=%s price=%s stop=%s hit=%s age=%.1fs", trade["symbol"], trade["decision"], price, trade["stop_level"], hit, data_age)
            if hit:
                close_active_trade("STOP_LEVEL_REACHED", price)
        except Exception:
            log.exception("Stop monitor iteration failed; will retry next cycle")

# ---------------------------- HTTP API for MT4/MT5 ----------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("HTTP: " + fmt, *args)

    def reply(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            log.warning("HTTP client disconnected before response was sent")

    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            return self.reply(200, {"ok": True, "service": "ZinoProSignalAI"})
        return self.reply(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        global last_signal_epoch, active_trade
        endpoint = self.path.split("?", 1)[0].rstrip("/")
        if endpoint not in ("/mt4", "/mt5"):
            return self.reply(404, {"ok": False, "error": "Unknown endpoint"})
        if not MT4_API_KEY:
            return self.reply(503, {"ok": False, "error": "MT4_API_KEY missing on Render"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 2_000_000:
            return self.reply(400, {"ok": False, "error": "Invalid body size"})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self.reply(400, {"ok": False, "error": "Invalid JSON"})
        if not isinstance(payload, dict):
            return self.reply(400, {"ok": False, "error": "JSON object required"})
        header_key = self.headers.get("X-API-Key", "")
        body_key = str(payload.get("api_key", ""))
        if header_key != MT4_API_KEY and body_key != MT4_API_KEY:
            return self.reply(401, {"ok": False, "error": "API key mismatch"})
        symbol = str(payload.get("symbol", "")).strip().upper()
        timeframe = str(payload.get("timeframe", "M1")).strip().upper()
        if not symbol or len(symbol) > 40:
            return self.reply(400, {"ok": False, "error": "Invalid symbol"})
        try:
            candles = normalize_candles(payload.get("candles"))
            last_candle = candles[-1]
            candle_time = last_candle["time"]
            market_key = f"{symbol}|{timeframe}"
            current_price = number(payload.get("price", payload.get("current_price")))
            # Refresh monitor data on every EA request, even if signal is throttled.
            with state_lock:
                latest_market[market_key] = {
                    "received_at": time.time(), "price": current_price,
                    "close": last_candle["close"], "candle_time": candle_time,
                    "low": last_candle["low"], "high": last_candle["high"],
                }
            signal_key = f"{symbol}|{timeframe}|{candle_time}"
            with signal_lock:
                cached = processed_signals.get(signal_key)
                if cached is not None:
                    return self.reply(200, {"ok": True, "duplicate": True, **cached})
                if signal_key in signals_in_progress:
                    return self.reply(200, {"ok": True, "duplicate": True, "processing": True, "symbol": symbol, "timeframe": timeframe})
                signals_in_progress.add(signal_key)
            generation_lock.acquire()
            try:
                with state_lock:
                    active = active_trade is not None
                    cooldown_left = max(0, SIGNAL_INTERVAL_SECONDS - (time.time() - last_signal_epoch))
                if active:
                    response_data = {"symbol": symbol, "timeframe": timeframe, "signal_sent": False, "reason": "active_trade_exists"}
                    log.info("SIGNAL SKIPPED | active trade still being monitored | %s %s", symbol, timeframe)
                    with signal_lock:
                        processed_signals[signal_key] = response_data
                    return self.reply(200, {"ok": True, **response_data})
                if cooldown_left > 0:
                    response_data = {"symbol": symbol, "timeframe": timeframe, "signal_sent": False, "reason": "three_minute_cooldown", "cooldown_seconds": int(cooldown_left)}
                    log.info("SIGNAL SKIPPED | cooldown %.1fs | %s %s", cooldown_left, symbol, timeframe)
                    with signal_lock:
                        processed_signals[signal_key] = response_data
                    return self.reply(200, {"ok": True, **response_data})

                features = market_features(candles)
                result = get_ai_signal(symbol, timeframe, candles, features)
                entry = suggested_entry_time(last_candle, timeframe)
                message = format_signal(symbol, timeframe, result, features, last_candle, entry)
                if not send_owner_threadsafe(message):
                    # Do not cache as processed; next EA cycle can retry delivery.
                    return self.reply(502, {"ok": False, "error": "Telegram delivery failed; retry next cycle"})

                now_epoch = time.time()
                stop_level = features["low"] if result["decision"] == "UP" else features["high"]
                with state_lock:
                    # Protect against simultaneous requests from several EA charts.
                    if active_trade is not None or now_epoch - last_signal_epoch < SIGNAL_INTERVAL_SECONDS:
                        response_data = {"symbol": symbol, "timeframe": timeframe, "signal_sent": True, "note": "signal delivered during concurrent request; cooldown now active"}
                    else:
                        last_signal_epoch = now_epoch
                        active_trade = {
                            "symbol": symbol, "timeframe": timeframe, "market_key": market_key,
                            "decision": result["decision"], "confidence": result["confidence"],
                            "entry_price": current_price if current_price is not None else features["close"],
                            "stop_level": stop_level, "created_at": now_epoch,
                            "expires_at": now_epoch + TRADE_DURATION_SECONDS,
                            "candle_time": candle_time,
                        }
                        response_data = {
                            "symbol": symbol, "timeframe": timeframe, "decision": result["decision"],
                            "confidence": result["confidence"], "telegram_delivered": True,
                            "signal_sent": True, "stop_level": stop_level,
                        }
                        log.info("SIGNAL SENT | symbol=%s timeframe=%s decision=%s confidence=%s entry=%s stop=%s candle_time=%s", symbol, timeframe, result["decision"], result["confidence"], active_trade["entry_price"], stop_level, candle_time)
                with stats_lock:
                    stats["signals"] += 1
                    stats["last_signal"] = {"symbol": symbol, "timeframe": timeframe, "decision": result["decision"], "confidence": result["confidence"], "candle_time": candle_time, "time": now_algiers().strftime("%Y-%m-%d %H:%M:%S")}
                with signal_lock:
                    processed_signals[signal_key] = response_data
                    while len(processed_signals) > MAX_SIGNAL_CACHE:
                        processed_signals.pop(next(iter(processed_signals)), None)
                return self.reply(200, {"ok": True, **response_data})
            finally:
                with signal_lock:
                    signals_in_progress.discard(signal_key)
                generation_lock.release()
        except ValueError as exc:
            return self.reply(400, {"ok": False, "error": str(exc)})
        except Exception:
            log.exception("MT4/MT5 request failed; next request can retry")
            return self.reply(500, {"ok": False, "error": "Internal server error"})


def run_http_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    log.info("HTTP server listening on port %s", PORT)
    while True:
        try:
            server.serve_forever(poll_interval=1)
        except Exception:
            log.exception("HTTP server error; restarting serve loop")
            time.sleep(3)

# ---------------------------- Owner commands ----------------------------
def is_owner(update):
    return bool(update.effective_user and update.effective_user.id == OWNER_ID)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update): return
    await update.effective_message.reply_text(
        "🎓 ZinoProSignalAI يعمل.\n\nMT4 endpoint: /mt4\nMT5 endpoint: /mt5\n"
        "الإشارة: مرة كل 3 دقائق كحد أدنى\nمدة الصفقة: دقيقة واحدة\nمراقبة الستوب: كل 30 ثانية\n"
        "/stats - الإحصائيات\n/win - تسجيل ربح\n/loss - تسجيل خسارة\n/reset - تصفير الإحصائيات"
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update): return
    with stats_lock: s = dict(stats)
    with state_lock: trade = dict(active_trade) if active_trade else None
    total = s["wins"] + s["losses"]
    rate = 100 * s["wins"] / total if total else 0
    last = s["last_signal"]
    last_text = "لا توجد إشارة بعد" if not last else f"{last['symbol']} {last['timeframe']} | {last['decision']} | {last['confidence']}% | {last['time']}"
    trade_text = "لا توجد صفقة تحت المراقبة" if not trade else f"{trade['symbol']} {trade['timeframe']} | {trade['decision']} | ينتهي خلال {max(0, int(trade['expires_at'] - time.time()))} ثانية"
    await update.effective_message.reply_text(
        "📊 ZinoProSignalAI STATS\n━━━━━━━━━━━━━━━━━━\n"
        f"📨 الإشارات المرسلة: {s['signals']}\n🟢 الأرباح المسجلة: {s['wins']}\n🔴 الخسائر المسجلة: {s['losses']}\n"
        f"🎯 نسبة الفوز المسجلة يدويًا: {rate:.1f}%\n🕒 آخر إشارة: {last_text}\n🔎 المراقبة: {trade_text}"
    )

async def cmd_win(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update): return
    with stats_lock: stats["wins"] += 1
    log.info("Manual WIN recorded by owner")
    await update.effective_message.reply_text("🟢 تم تسجيل ربح.")

async def cmd_loss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update): return
    with stats_lock: stats["losses"] += 1
    log.info("Manual LOSS recorded by owner")
    await update.effective_message.reply_text("🔴 تم تسجيل خسارة.")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update): return
    with stats_lock: stats.update({"signals": 0, "wins": 0, "losses": 0, "last_signal": None})
    log.info("Statistics reset by owner")
    await update.effective_message.reply_text("♻️ تم تصفير الإحصائيات.")

async def post_init(application):
    global telegram_loop
    telegram_loop = asyncio.get_running_loop()
    log.info("Telegram initialized")


def main():
    global telegram_app
    if not BOT_TOKEN: raise RuntimeError("Set BOT_TOKEN in Render environment variables")
    if not OWNER_ID: raise RuntimeError("Set OWNER_ID to your numeric Telegram user ID")
    if not MT4_API_KEY: log.warning("MT4_API_KEY is missing")
    if not GEMINI_API_KEY: log.warning("GEMINI_API_KEY missing; fallback analysis enabled")
    threading.Thread(target=run_http_server, daemon=True, name="http-server").start()
    threading.Thread(target=stop_monitor_loop, daemon=True, name="stop-monitor").start()
    telegram_app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CommandHandler("stats", cmd_stats))
    telegram_app.add_handler(CommandHandler("win", cmd_win))
    telegram_app.add_handler(CommandHandler("loss", cmd_loss))
    telegram_app.add_handler(CommandHandler("reset", cmd_reset))
    log.info("Starting ZinoProSignalAI | signal interval=%ss | trade duration=%ss | stop monitor=%ss", SIGNAL_INTERVAL_SECONDS, TRADE_DURATION_SECONDS, STOP_MONITOR_SECONDS)
    telegram_app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
