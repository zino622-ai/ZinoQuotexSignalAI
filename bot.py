import os
import json
import re
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from google import genai
from google.genai import types


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

OWNER_ID = int(OWNER_ID)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("ZinoQuotexSignalAI")

client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# SIMPLE HEALTH SERVER FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ZinoQuotexSignalAI is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


Thread(target=start_health_server, daemon=True).start()


# ============================================================
# TRADE STATS
# ============================================================

stats = {
    "total": 0,
    "wins": 0,
    "losses": 0,
}


# ============================================================
# OWNER CHECK
# ============================================================

def is_owner(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == OWNER_ID


async def reject_if_not_owner(update: Update) -> bool:
    if not is_owner(update):
        if update.message:
            await update.message.reply_text("⛔ هذا البوت خاص.")
        return True
    return False


# ============================================================
# JSON EXTRACTION
# ============================================================

def extract_json(text: str):
    """
    Gemini may sometimes wrap JSON in markdown.
    This function extracts the first valid JSON object.
    """

    if not text:
        return None

    text = text.strip()

    # Remove markdown fences
    text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```\s*", "", text)

    # Direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Search for JSON object
    start = text.find("{")

    while start != -1:
        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            char = text[i]

            if escape:
                escape = False
                continue

            if char == "\\":
                escape = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == "{":
                depth += 1

            elif char == "}":
                depth -= 1

                if depth == 0:
                    candidate = text[start:i + 1]

                    try:
                        return json.loads(candidate)
                    except Exception:
                        break

        start = text.find("{", start + 1)

    return None


# ============================================================
# NORMALIZE ANALYSIS
# ============================================================

def normalize_analysis(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Invalid analysis object")

    direction = str(
        data.get("direction")
        or data.get("decision")
        or ""
    ).upper().strip()

    if direction in ["CALL", "UP", "BUY", "LONG"]:
        direction = "UP"
    elif direction in ["PUT", "DOWN", "SELL", "SHORT"]:
        direction = "DOWN"
    else:
        raise ValueError("Invalid direction")

    try:
        confidence = int(float(data.get("confidence", 0)))
    except Exception:
        confidence = 0

    try:
        forecast = int(float(data.get("forecast", confidence)))
    except Exception:
        forecast = confidence

    try:
        strength = int(float(data.get("strength", confidence)))
    except Exception:
        strength = confidence

    confidence = max(50, min(99, confidence))
    forecast = max(50, min(99, forecast))
    strength = max(50, min(99, strength))

    # Only 1 or 2 minutes are allowed.
    try:
        entry_delay = int(float(data.get("entry_delay", 1)))
    except Exception:
        entry_delay = 1

    if entry_delay not in [1, 2]:
        entry_delay = 1

    asset = str(data.get("asset", "UNKNOWN")).strip()
    timeframe = str(data.get("timeframe", "2M")).strip()

    entry_price = str(
        data.get("entry_price")
        or data.get("price")
        or "N/A"
    ).strip()

    trend = str(data.get("trend", "Neutral")).strip()
    momentum = str(data.get("momentum", "Neutral")).strip()
    structure = str(data.get("structure", "Neutral")).strip()
    confirmation = str(data.get("confirmation", "None")).strip()
    reason = str(data.get("reason", "")).strip()

    return {
        "direction": direction,
        "confidence": confidence,
        "forecast": forecast,
        "strength": strength,
        "entry_delay": entry_delay,
        "asset": asset,
        "timeframe": timeframe,
        "entry_price": entry_price,
        "trend": trend,
        "momentum": momentum,
        "structure": structure,
        "confirmation": confirmation,
        "reason": reason,
    }


# ============================================================
# TIME CALCULATION
# QUOTEX USER TIMEZONE = UTC-3
# ============================================================

def get_entry_time(delay_minutes: int) -> str:
    utc_now = datetime.now(timezone.utc)

    quotex_tz = timezone(timedelta(hours=-3))

    now = utc_now.astimezone(quotex_tz)

    entry = now + timedelta(minutes=delay_minutes)

    return entry.strftime("%H:%M")


# ============================================================
# PRICE DISPLAY
# ============================================================

def clean_price(price: str) -> str:
    """
    Keeps the price as Gemini gives it.
    Does not artificially round it to 5 digits.
    """

    if not price:
        return "N/A"

    price = str(price).strip()

    # Remove accidental text
    price = price.replace(",", "")

    # Extract number
    match = re.search(r"-?\d+(?:\.\d+)?", price)

    if match:
        return match.group(0)

    return price


# ============================================================
# GEMINI ANALYSIS PROMPT
# ============================================================

SYSTEM_PROMPT = r"""
You are ZINO, a professional visual price-action analyzer for Quotex charts.

Your job is NOT to predict randomly.

Analyze the screenshot deeply and choose ONE direction:

UP or DOWN.

IMPORTANT:
The chart timeframe is usually 2M, but the screenshot may visually show another timeframe.
Use the actual visible chart information when available.

ENTRY TIMING:
You MUST choose either:
1 = enter at the beginning of the next suitable candle, approximately 1 minute later
2 = wait approximately 2 minutes and enter at the beginning of the later suitable candle

Never return 3, 4, 5 or more minutes.

Choose 2 minutes only when the current setup needs more confirmation or the immediate next candle is likely to be noisy.
Choose 1 minute when the structure and confirmation are already clear.

CORE ANALYSIS:

1. CANDLE STRUCTURE
- Open
- Close
- High
- Low
- Candle bodies
- Wicks
- Strong bullish/bearish closes
- Rejection candles
- Hammer / pin bar
- Engulfing candles

2. MARKET STRUCTURE
For bullish structure look for:
- Higher High
- Higher Low
- Bullish breakout
- Holding above an important area

For bearish structure look for:
- Lower High
- Lower Low
- Bearish breakdown
- Holding below an important area

Do NOT call UP simply because the latest candle is green.
Do NOT call DOWN simply because the latest candle is red.

3. BREAKOUT
Check:
- Is there a real breakout?
- Did the candle close beyond the structure?
- Is it only a wick?
- Is there a fake breakout?
- Is price returning into the previous range?

4. LIQUIDITY / SWEEP
Look for:
- Sweep of recent highs
- Sweep of recent lows
- Rejection after liquidity grab
- Break and reclaim
- Break and rejection

5. MOMENTUM
Determine whether momentum supports UP or DOWN.

6. CONFIRMATION
A direction becomes stronger when multiple independent clues agree.

7. CONSOLIDATION
If price is moving horizontally:
- Do not automatically choose the latest candle direction.
- Check the range boundaries.
- Check rejection and breakout behavior.

8. BALANCED DIRECTION
You must independently test both possibilities:

UP CASE:
What evidence supports UP?

DOWN CASE:
What evidence supports DOWN?

Then choose the side with stronger evidence.

Never force UP.
Never force DOWN.

9. FORECAST
Forecast is an analytical confidence score, NOT a guaranteed probability of winning.

Use:
50-59 = weak setup
60-69 = moderate setup
70-79 = good setup
80-89 = strong setup
90-99 = very strong visual agreement

Do not give 90+ just because the signal looks interesting.
90+ requires several independent pieces of evidence agreeing.

10. CONFIDENCE
Confidence should reflect the total agreement of:
- structure
- candles
- breakout/rejection
- momentum
- confirmation
- timing

11. STRENGTH
Strength reflects how clean and strong the current setup is.

12. ENTRY PRICE
Read the visible current/appropriate price from the chart when possible.

VERY IMPORTANT:
Do not invent an entry price if the chart does not clearly show one.
Return "N/A" if it cannot be reliably read.

13. ASSET
Read the asset/pair from the screenshot if visible.

14. TIMEFRAME
Return the visible timeframe if readable.
If not readable, use "2M".

OUTPUT:
Return ONLY valid JSON.

No markdown.
No explanation outside JSON.
No code fences.

Exact JSON structure:

{
  "direction": "UP",
  "confidence": 82,
  "forecast": 84,
  "strength": 78,
  "entry_delay": 1,
  "asset": "GBP/USD",
  "timeframe": "2M",
  "entry_price": "1.3516",
  "trend": "Bullish",
  "momentum": "Bullish",
  "structure": "Higher High / Higher Low",
  "confirmation": "Bullish candle close after breakout",
  "reason": "Short concise reason based only on visible chart evidence"
}

Rules:
- direction must be exactly UP or DOWN.
- confidence must be integer 50-99.
- forecast must be integer 50-99.
- strength must be integer 50-99.
- entry_delay must be exactly 1 or 2.
- Never return null.
- Never return an expiry time.
- Never return a stop loss.
- Never return a martingale instruction.
"""


# ============================================================
# GEMINI CALL
# ============================================================

async def analyze_chart(image_bytes: bytes) -> dict:

    image = Image.open(__import__("io").BytesIO(image_bytes))

    # IMPORTANT:
    # No resizing. Original screenshot quality is preserved.

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg",
            ),
            SYSTEM_PROMPT,
        ],
        config=types.GenerateContentConfig(
            temperature=0.15,
            max_output_tokens=700,
            response_mime_type="application/json",
        ),
    )

    raw = getattr(response, "text", "") or ""

    data = extract_json(raw)

    if not data:
        raise ValueError("Gemini returned invalid JSON")

    return normalize_analysis(data)


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(a: dict) -> str:

    direction = a["direction"]

    if direction == "UP":
        emoji = "🟢"
        arabic = "شراء / CALL"
    else:
        emoji = "🔴"
        arabic = "بيع / PUT"

    delay = a["entry_delay"]
    entry_time = get_entry_time(delay)

    price = clean_price(a["entry_price"])

    return (
        "🎓 تحليل زينو\n\n"
        f"🎯 القرار: {emoji} {direction} — {arabic}\n"
        f"📊 Confidence: {a['confidence']}%\n"
        f"🔮 Forecast: {a['forecast']}%\n"
        f"💪 Strength: {a['strength']}%\n"
        "━━━━━━━━━━━━━━\n"
        f"📊 {a['asset']} · ⏱ {a['timeframe']}\n"
        f"💰 سعر الدخول: {price}\n"
        f"🕐 وقت الدخول: {entry_time}\n"
        f"⏳ التأخير: {delay} دقيقة\n"
        "━━━━━━━━━━━━━━\n"
        f"📈 Trend: {a['trend']}\n"
        f"📊 Momentum: {a['momentum']}\n"
        f"🏗 Structure: {a['structure']}\n"
        f"🕯 Confirmation: {a['confirmation']}\n"
        "━━━━━━━━━━━━━━\n"
        f"🧠 السبب: {a['reason']}"
    )


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if await reject_if_not_owner(update):
        return

    await update.message.reply_text(
        "🎓 أهلاً بك في ZinoQuotexSignalAI\n\n"
        "📸 أرسل لقطة شاشة للشارت.\n"
        "⏱ البوت يختار تلقائيًا الدخول بعد 1 أو 2 دقيقة حسب التحليل.\n\n"
        "الأوامر:\n"
        "/stats — الإحصائيات\n"
        "/reset — تصفير الإحصائيات"
    )


# ============================================================
# STATS
# ============================================================

def stats_text() -> str:

    total = stats["total"]
    wins = stats["wins"]
    losses = stats["losses"]

    if total > 0:
        rate = (wins / total) * 100
    else:
        rate = 0

    return (
        "📊 إحصائيات زينو\n"
        "━━━━━━━━━━━━━━\n"
        f"📌 الصفقات: {total}\n"
        f"🟢 WIN: {wins}\n"
        f"🔴 LOSS: {losses}\n"
        f"🎯 Win Rate: {rate:.1f}%"
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if await reject_if_not_owner(update):
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 WIN", callback_data="win"),
            InlineKeyboardButton("🔴 LOSS", callback_data="loss"),
        ]
    ])

    await update.message.reply_text(
        stats_text(),
        reply_markup=keyboard,
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if await reject_if_not_owner(update):
        return

    stats["total"] = 0
    stats["wins"] = 0
    stats["losses"] = 0

    await update.message.reply_text(
        "♻️ تم تصفير الإحصائيات."
    )


# ============================================================
# BUTTONS
# ============================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        return

    if query.data == "win":
        stats["total"] += 1
        stats["wins"] += 1

    elif query.data == "loss":
        stats["total"] += 1
        stats["losses"] += 1

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 WIN", callback_data="win"),
            InlineKeyboardButton("🔴 LOSS", callback_data="loss"),
        ]
    ])

    await query.edit_message_text(
        stats_text(),
        reply_markup=keyboard,
    )


# ============================================================
# IMAGE HANDLER
# ============================================================

async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if await reject_if_not_owner(update):
        return

    message = update.message

    status = await message.reply_text(
        "🔍 جاري تحليل الشارت..."
    )

    try:

        photo = message.photo[-1]

        telegram_file = await photo.get_file()

        image_bytes = await telegram_file.download_as_bytearray()

        analysis = await analyze_chart(bytes(image_bytes))

        result = format_signal(analysis)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟢 WIN", callback_data="win"),
                InlineKeyboardButton("🔴 LOSS", callback_data="loss"),
            ]
        ])

        await status.edit_text(
            result,
            reply_markup=keyboard,
        )

    except Exception as e:

        logger.exception("Analysis error")

        error_text = str(e)

        if len(error_text) > 500:
            error_text = error_text[:500]

        await status.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{error_text}"
        )


# ============================================================
# TEXT HANDLER
# ============================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if await reject_if_not_owner(update):
        return

    await update.message.reply_text(
        "📸 أرسل صورة الشارت حتى أحللها."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info("Starting ZinoQuotexSignalAI...")
    logger.info("Gemini model: %s", GEMINI_MODEL)

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("stats", stats_command)
    )

    application.add_handler(
        CommandHandler("reset", reset_command)
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            image_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
