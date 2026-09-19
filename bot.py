import os
import json
import re
import logging
import threading
import io
from datetime import datetime, timedelta, timezone
from PIL import Image
from google import genai
from google.genai import types
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")

# Render Environment Variable overrides this value.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

PORT = int(os.getenv("PORT", "10000"))

# Quotex timezone requested by user: UTC-3
USER_TIMEZONE = timezone(timedelta(hours=-3))

TIMEFRAME = "2M"


# ============================================================
# ENVIRONMENT CHECK
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

try:
    OWNER_ID = int(OWNER_ID)
except ValueError:
    raise RuntimeError("OWNER_ID must be an integer")


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("ZinoQuotexSignalAI")


# ============================================================
# GEMINI
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# STATS
# ============================================================

stats = {
    "total": 0,
    "wins": 0,
    "losses": 0,
}


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()

        self.wfile.write(
            b"ZinoQuotexSignalAI is running"
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()

    logger.info(
        "Health server running on port %s",
        PORT
    )


# ============================================================
# OWNER CHECK
# ============================================================

def is_owner(update: Update) -> bool:

    if not update.effective_user:
        return False

    return update.effective_user.id == OWNER_ID


# ============================================================
# TIME
# ============================================================

def now_user_time():

    return datetime.now(USER_TIMEZONE)


def next_2m_candle():

    now = now_user_time()

    minute = now.minute

    next_minute = minute + (2 - minute % 2)

    if next_minute >= 60:

        target = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

    else:

        target = now.replace(
            minute=next_minute,
            second=0,
            microsecond=0
        )

    if target <= now:
        target += timedelta(minutes=2)

    return target


# ============================================================
# GEMINI PROMPT
# ============================================================

SYSTEM_PROMPT = r"""
You are Zino, a professional short-term Quotex chart analyst.

Analyze ONLY the chart image provided.

The chart timeframe is 2 minutes.

The user's timezone is UTC-3.

Your task is to determine whether the next meaningful short-term movement is:

UP = CALL
DOWN = PUT

Never return NO SIGNAL.

However, do not give high confidence unless the chart provides real confirmation.

============================================================
IMPORTANT ANALYSIS RULES
============================================================

1. PRICE ACTION FIRST

Prioritize:

- candle open
- candle close
- candle highs
- candle lows
- higher highs
- higher lows
- lower highs
- lower lows
- breakouts
- failed breakouts
- rejection candles
- momentum
- market structure

Do not base the signal on one indicator alone.

------------------------------------------------------------

2. MARKET STRUCTURE

Bullish structure:

- higher highs
- higher lows
- bullish breakout
- bullish continuation

Bearish structure:

- lower highs
- lower lows
- bearish breakout
- bearish continuation

Sideways:

- repeated movement inside a horizontal range
- no clear directional structure

When the market is sideways, require stronger candle confirmation.

------------------------------------------------------------

3. CANDLE CONFIRMATION

Look for:

- strong bullish close
- strong bearish close
- rejection
- hammer
- shooting star
- bullish engulfing
- bearish engulfing
- breakout candle
- failed breakout

A small weak candle is NOT strong confirmation.

------------------------------------------------------------

4. BREAKOUTS

Do not immediately chase a breakout.

Check:

- whether the candle actually closed beyond the level
- whether the breakout has momentum
- whether the next candle is likely to continue
- whether the breakout occurred after consolidation
- whether price is already heavily extended

A breakout followed by immediate rejection reduces confidence.

------------------------------------------------------------

5. KELTNER CHANNEL

Use Keltner as supporting evidence only.

Check:

- upper band
- middle band
- lower band
- rejection from a band
- movement through the middle
- channel direction

Do not generate a signal only because price touched a Keltner band.

------------------------------------------------------------

6. ADX / DI

Use ADX and DI as supporting confirmation.

Strong trend:

- rising ADX
- clear DI separation

Weak trend:

- low ADX
- DI lines close together
- frequent crossing

If ADX is weak, do not give excessive confidence.

------------------------------------------------------------

7. AVOID CHASING

If price has already made a large move immediately before the signal:

reduce confidence.

Prefer a controlled continuation or confirmed pullback.

------------------------------------------------------------

8. COUNTER-TREND SIGNALS

If the main trend is Bullish and you want DOWN:

you need strong reversal evidence.

If the main trend is Bearish and you want UP:

you need strong reversal evidence.

Do not choose a counter-trend signal only because of one red or green candle.

------------------------------------------------------------

9. CONFIDENCE

Confidence must be realistic.

50-59:
weak setup

60-69:
moderate setup

70-79:
strong setup with multiple confirmations

80-85:
very strong alignment only

Never give 70%+ simply because one candle looks strong.

------------------------------------------------------------

10. ENTRY

The entry must be in the future.

Never return an entry time earlier than the current chart time.

The preferred entry is the beginning of a future 2-minute candle.

Return:

entry_delay_minutes

Allowed values:

1
2
3

Prefer 2 when the current candle is too close to closing and the next candle gives a cleaner entry.

Do not return 0.

------------------------------------------------------------

11. ENTRY PRICE

Read the price directly from the chart if clearly visible.

Do not invent unnecessary decimal precision.

The price format must match the price format shown on the chart.

IMPORTANT:

The final displayed price must contain EXACTLY 6 DIGITS TOTAL.

Example:

287500

Not:

287.500000
287.50
287500.000000

Another example:

123456

Another:

098765

If the chart clearly shows a 6-digit price format, preserve it exactly.

------------------------------------------------------------

12. CANCELLATION PRICE

The cancellation price must:

- be below the entry price
- contain exactly 6 digits total
- use the same price format as the entry price
- be a realistic nearby price visible/consistent with the chart

Example:

Entry:
287500

Cancellation:
287450

DO NOT return:

287.450000
287.45
287450.000000

The cancellation price must be numerically lower than the entry price.

------------------------------------------------------------

13. OUTPUT

Return ONLY valid JSON.

No markdown.

No explanation outside JSON.

Use exactly these fields:

{
  "direction": "UP",
  "confidence": 68,
  "asset": "USD/PKR",
  "timeframe": "2M",
  "general_trend": "Sideways",
  "short_trend": "Bullish",
  "entry_price": "287500",
  "cancellation_price": "287450",
  "entry_delay_minutes": 2,
  "structure": "Consolidation with recent higher lows",
  "keltner": "Price moving from the lower-middle area toward the upper band",
  "adx": "Neutral ADX with short-term bullish DI alignment",
  "confirmation_candle": "Strong bullish candle closing near its high",
  "reason": "Short-term bullish momentum supported by higher lows and candle confirmation"
}

Rules:

direction must be exactly:

UP

or

DOWN

confidence must be integer 50-85.

entry_delay_minutes must be:

1, 2, or 3.

entry_price must be a string containing exactly 6 digits.

cancellation_price must be a string containing exactly 6 digits.

cancellation_price must be lower than entry_price.

No decimals.

No commas.

No spaces inside the price.

Return valid JSON only.
"""


# ============================================================
# JSON EXTRACTION
# ============================================================

def extract_json(text):

    if not text:
        return None

    text = text.strip()

    # Remove markdown fences if Gemini adds them
    text = re.sub(
        r"^```(?:json)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"```$",
        "",
        text
    ).strip()

    try:
        return json.loads(text)

    except Exception:
        pass

    match = re.search(
        r"\{.*\}",
        text,
        flags=re.DOTALL
    )

    if match:

        try:
            return json.loads(
                match.group(0)
            )

        except Exception:
            return None

    return None


# ============================================================
# IMAGE
# ============================================================

def image_to_bytes(image):

    buffer = io.BytesIO()

    # Keep original resolution.
    # Do NOT resize the chart.

    if image.mode not in ("RGB", "RGBA"):

        image = image.convert("RGB")

    image.save(
        buffer,
        format="PNG"
    )

    return buffer.getvalue()


# ============================================================
# GEMINI ANALYSIS
# ============================================================

def analyze_image(image):

    response = client.models.generate_content(

        model=GEMINI_MODEL,

        contents=[

            types.Part.from_bytes(

                data=image_to_bytes(image),

                mime_type="image/png"
            ),

            SYSTEM_PROMPT,
        ],

        config=types.GenerateContentConfig(

            temperature=0.10,

            max_output_tokens=1000,

            response_mime_type="application/json",
        ),
    )

    text = getattr(
        response,
        "text",
        None
    )

    if not text:

        raise RuntimeError(
            "Gemini returned an empty response"
        )

    data = extract_json(text)

    if not data:

        raise RuntimeError(
            "Gemini returned invalid JSON"
        )

    return data


# ============================================================
# PRICE FORMAT
# ============================================================

def normalize_six_digit_price(value):

    """
    Converts a Gemini price into exactly 6 digits.

    Examples:

    287500 -> 287500
    "287500" -> 287500

    If Gemini returns decimal notation,
    the function removes punctuation and
    tries to preserve six digits.
    """

    if value is None:
        raise ValueError("Price is missing")

    raw = str(value).strip()

    # Remove common formatting
    raw = raw.replace(",", "")
    raw = raw.replace(" ", "")

    # Already exactly six digits
    if re.fullmatch(r"\d{6}", raw):

        return raw

    # If decimal notation was returned,
    # extract digits.
    digits = re.sub(
        r"\D",
        "",
        raw
    )

    if len(digits) == 6:

        return digits

    # If fewer than 6 digits, pad on the right.
    if len(digits) < 6:

        digits = digits.ljust(
            6,
            "0"
        )

        return digits

    # If more than 6 digits,
    # keep the first six.
    return digits[:6]


# ============================================================
# VALIDATE ANALYSIS
# ============================================================

def validate_analysis(data):

    direction = str(
        data.get(
            "direction",
            ""
        )
    ).upper().strip()

    if direction not in (
        "UP",
        "DOWN"
    ):

        raise RuntimeError(
            "Gemini returned invalid direction"
        )

    # Confidence
    try:

        confidence = int(
            data.get(
                "confidence",
                50
            )
        )

    except Exception:

        confidence = 50

    confidence = max(
        50,
        min(
            85,
            confidence
        )
    )

    # Prices
    try:

        entry_price = normalize_six_digit_price(
            data.get("entry_price")
        )

        cancellation_price = normalize_six_digit_price(
            data.get("cancellation_price")
        )

    except Exception as e:

        raise RuntimeError(
            "Gemini returned invalid price format"
        ) from e

    # Ensure cancellation is BELOW entry.
    entry_number = int(entry_price)
    cancellation_number = int(
        cancellation_price
    )

    if cancellation_number >= entry_number:

        # Create a realistic lower level.
        cancellation_number = max(
            0,
            entry_number - 50
        )

        cancellation_price = str(
            cancellation_number
        ).zfill(6)

    # Entry delay
    try:

        delay = int(
            data.get(
                "entry_delay_minutes",
                2
            )
        )

    except Exception:

        delay = 2

    delay = max(
        1,
        min(
            3,
            delay
        )
    )

    data["direction"] = direction
    data["confidence"] = confidence

    data["entry_price"] = entry_price
    data["cancellation_price"] = cancellation_price

    data["entry_delay_minutes"] = delay

    return data


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(data):

    direction = data["direction"]

    if direction == "UP":

        emoji = "🟢"

        label = "UP — شراء (Call)"

    else:

        emoji = "🔴"

        label = "DOWN — بيع (Put)"

    confidence = data["confidence"]

    asset = data.get(
        "asset",
        "Unknown"
    )

    timeframe = data.get(
        "timeframe",
        TIMEFRAME
    )

    general_trend = data.get(
        "general_trend",
        "Unknown"
    )

    short_trend = data.get(
        "short_trend",
        "Unknown"
    )

    structure = data.get(
        "structure",
        "Price structure analyzed"
    )

    keltner = data.get(
        "keltner",
        "Keltner used as supporting factor"
    )

    adx = data.get(
        "adx",
        "ADX used as supporting factor"
    )

    candle = data.get(
        "confirmation_candle",
        "Candle confirmation analyzed"
    )

    reason = data.get(
        "reason",
        "Multiple price-action factors are aligned"
    )

    entry_price = data[
        "entry_price"
    ]

    cancellation_price = data[
        "cancellation_price"
    ]

    delay = data[
        "entry_delay_minutes"
    ]

    # Current time
    now = now_user_time()

    # Future entry
    entry_time = (
        now +
        timedelta(
            minutes=delay
        )
    )

    # Round to minute
    entry_time = entry_time.replace(
        second=0,
        microsecond=0
    )

    chart_time = now.strftime(
        "%H:%M:%S"
    ) + " UTC-3"

    entry_time_text = entry_time.strftime(
        "%H:%M:%S"
    ) + " UTC-3"

    message = f"""
🎓 تحليل زينو

🎯 Confidence: {confidence}%
📊 {asset} · ⏱ {timeframe}
━━━━━━━━━━━━━━

🎯 القرار: {emoji} {label}
🕐 وقت الشارت: {chart_time}
⏰ وقت الدخول: {entry_time_text}
💵 سعر الدخول: {entry_price}
🛑 إلغاء إذا أغلقت شمعة تحت {cancellation_price}
━━━━━━━━━━━━━━

📐 الاتجاه: {general_trend}
📈 الاتجاه القصير: {short_trend}
🏗 البنية السعرية: {structure}
〽️ Keltner 20/10: {keltner}
📊 ADX 14/14: {adx}
🕯 شمعة التأكيد: {candle}
━━━━━━━━━━━━━━

🧠 شرح زينو: {reason}

━━━━━━━━━━━━━━
⚠️ التحليل مبني على الشارت المرسل فقط.
"""

    return message.strip()


# ============================================================
# BUTTONS
# ============================================================

def result_keyboard():

    keyboard = [

        [

            InlineKeyboardButton(
                "✅ WIN",
                callback_data="trade_win"
            ),

            InlineKeyboardButton(
                "❌ LOSS",
                callback_data="trade_loss"
            ),

        ],

        [

            InlineKeyboardButton(
                "📊 الإحصائيات",
                callback_data="trade_stats"
            ),

        ]

    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# ============================================================
# STATS
# ============================================================

def stats_text():

    total = stats["total"]

    wins = stats["wins"]

    losses = stats["losses"]

    if total > 0:

        winrate = (
            wins /
            total
        ) * 100

    else:

        winrate = 0

    return (
        "📊 إحصائيات زينو\n"
        "━━━━━━━━━━━━━━\n"
        f"🎯 الصفقات: {total}\n"
        f"✅ WIN: {wins}\n"
        f"❌ LOSS: {losses}\n"
        f"📈 Win Rate: {winrate:.1f}%"
    )


# ============================================================
# START
# ============================================================

async def start_command(
    update,
    context
):

    if not is_owner(update):
        return

    await update.message.reply_text(

        "🎓 ZinoQuotexSignalAI\n\n"

        "📸 أرسل صورة الشارت لتحليلها.\n\n"

        "⏱ الإطار: 2M\n"
        "🌍 التوقيت: UTC-3\n\n"

        "📊 /stats\n"
        "♻️ /reset"

    )


# ============================================================
# STATS COMMAND
# ============================================================

async def stats_command(
    update,
    context
):

    if not is_owner(update):
        return

    await update.message.reply_text(
        stats_text()
    )


# ============================================================
# RESET
# ============================================================

async def reset_command(
    update,
    context
):

    if not is_owner(update):
        return

    stats["total"] = 0
    stats["wins"] = 0
    stats["losses"] = 0

    await update.message.reply_text(
        "♻️ تم تصفير إحصائيات الصفقات."
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def photo_handler(
    update,
    context
):

    if not is_owner(update):
        return

    if (
        not update.message
        or not update.message.photo
    ):
        return

    status_message = (
        await update.message.reply_text(
            "🔎 جاري تحليل الشارت..."
        )
    )

    try:

        # Get highest-resolution Telegram photo
        photo = update.message.photo[-1]

        file = await context.bot.get_file(
            photo.file_id
        )

        image_bytes = (
            await file.download_as_bytearray()
        )

        image = Image.open(
            io.BytesIO(image_bytes)
        )

        image.load()

        # Keep original image resolution.
        if image.mode not in (
            "RGB",
            "RGBA"
        ):

            image = image.convert(
                "RGB"
            )

        # Gemini analysis
        data = analyze_image(
            image
        )

        # Validate
        data = validate_analysis(
            data
        )

        # Format
        signal_message = format_signal(
            data
        )

        await status_message.edit_text(

            signal_message,

            reply_markup=result_keyboard()

        )

    except Exception as e:

        logger.exception(
            "Analysis error"
        )

        error_text = str(e)

        if len(error_text) > 1000:

            error_text = (
                error_text[:1000]
            )

        await status_message.edit_text(

            "❌ حدث خطأ أثناء التحليل:\n\n"
            + error_text

        )


# ============================================================
# CALLBACKS
# ============================================================

async def callback_handler(
    update,
    context
):

    if not update.callback_query:
        return

    query = update.callback_query

    await query.answer()

    if not update.effective_user:
        return

    if update.effective_user.id != OWNER_ID:
        return

    action = query.data

    # WIN
    if action == "trade_win":

        stats["total"] += 1

        stats["wins"] += 1

        await query.edit_message_reply_markup(
            reply_markup=None
        )

        await query.message.reply_text(

            "✅ تم تسجيل WIN\n\n"
            + stats_text()

        )

    # LOSS
    elif action == "trade_loss":

        stats["total"] += 1

        stats["losses"] += 1

        await query.edit_message_reply_markup(
            reply_markup=None
        )

        await query.message.reply_text(

            "❌ تم تسجيل LOSS\n\n"
            + stats_text()

        )

    # STATS
    elif action == "trade_stats":

        await query.message.reply_text(
            stats_text()
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    context
):

    error = context.error

    logger.error(
        "Telegram error: %s",
        error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    start_health_server()

    logger.info(
        "ZinoQuotexSignalAI started."
    )

    logger.info(
        "Gemini model: %s",
        GEMINI_MODEL
    )

    application = (

        ApplicationBuilder()

        .token(BOT_TOKEN)

        .connect_timeout(30)

        .read_timeout(30)

        .write_timeout(30)

        .pool_timeout(30)

        .get_updates_connect_timeout(30)

        .get_updates_read_timeout(30)

        .get_updates_write_timeout(30)

        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats_command
        )
    )

    application.add_handler(
        CommandHandler(
            "reset",
            reset_command
        )
    )

    # Photos
    application.add_handler(

        MessageHandler(
            filters.PHOTO,
            photo_handler
        )

    )

    # Buttons
    application.add_handler(

        CallbackQueryHandler(
            callback_handler
        )

    )

    application.add_error_handler(
        error_handler
    )

    # Only Render instance should run polling.
    application.run_polling(

        drop_pending_updates=True,

        allowed_updates=Update.ALL_TYPES

    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
