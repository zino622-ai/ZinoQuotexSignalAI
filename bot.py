import os
import io
import json
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from google import genai
from google.genai import types
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# ============================================================
# ENV
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash-lite"
)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

OWNER_ID = int(OWNER_ID)


# ============================================================
# GEMINI CLIENT
# ============================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain"
        )
        self.end_headers()
        self.wfile.write(
            b"ZinoQuotexSignalAI is running"
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.getenv("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    server.serve_forever()


threading.Thread(
    target=start_health_server,
    daemon=True
).start()


# ============================================================
# STATS
# ============================================================

stats = {
    "WIN": 0,
    "LOSS": 0,
}


# ============================================================
# OWNER
# ============================================================

def is_owner(user_id):

    return user_id == OWNER_ID


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_owner(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "⛔ هذا البوت خاص."
        )
        return

    await update.message.reply_text(
        "🎓 Zino Signal AI\n\n"
        "📸 أرسل صورة الشارت."
    )


# ============================================================
# STATS
# ============================================================

async def show_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_owner(
        update.effective_user.id
    ):
        return

    total = (
        stats["WIN"]
        + stats["LOSS"]
    )

    rate = (
        stats["WIN"] / total * 100
        if total
        else 0
    )

    await update.message.reply_text(
        "📊 إحصائيات الصفقات\n"
        "━━━━━━━━━━━━━━\n\n"
        f"✅ WIN: {stats['WIN']}\n"
        f"❌ LOSS: {stats['LOSS']}\n"
        f"📌 Total: {total}\n"
        f"🎯 Win Rate: {rate:.1f}%"
    )


# ============================================================
# RESET
# ============================================================

async def reset_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_owner(
        update.effective_user.id
    ):
        return

    stats["WIN"] = 0
    stats["LOSS"] = 0

    await update.message.reply_text(
        "♻️ تم تصفير الإحصائيات."
    )


# ============================================================
# WIN / LOSS BUTTONS
# ============================================================

async def result_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    if not is_owner(
        query.from_user.id
    ):
        return

    if query.data == "WIN":

        stats["WIN"] += 1
        result = "✅ WIN"

    elif query.data == "LOSS":

        stats["LOSS"] += 1
        result = "❌ LOSS"

    else:
        return

    total = (
        stats["WIN"]
        + stats["LOSS"]
    )

    rate = (
        stats["WIN"] / total * 100
        if total
        else 0
    )

    await query.edit_message_reply_markup(
        reply_markup=None
    )

    await query.message.reply_text(
        f"{result} تم تسجيلها.\n\n"
        f"📊 WIN: {stats['WIN']}\n"
        f"❌ LOSS: {stats['LOSS']}\n"
        f"🎯 Win Rate: {rate:.1f}%"
    )


# ============================================================
# IMAGE PREPARATION
# ============================================================

def image_bytes(image):

    buffer = io.BytesIO()

    image.convert("RGB").save(
        buffer,
        format="JPEG",
        quality=90
    )

    return buffer.getvalue()


# ============================================================
# FAST GEMINI ANALYSIS
# ============================================================

async def analyze_chart(image):

    prompt = """
You are Zino Fast Signal AI.

Analyze this Quotex chart quickly.

Focus ONLY on:
- candle direction
- candle closes
- highs/lows
- market structure
- breakout or rejection
- momentum
- visible trend
- visible RSI if available

Consider BOTH UP and DOWN.

Choose exactly one:
UP or DOWN.

Do NOT return NO SIGNAL.

Do NOT invent the entry price.
Read the current visible price from the chart.
If unreadable use UNKNOWN.

The entry is for the beginning of the next candle.

Return ONLY valid JSON.
No markdown.
No explanation.

Use exactly:

{
"asset":"GBP/USD",
"timeframe":"2M",
"direction":"UP",
"confidence":84,
"strength":69,
"entry_price":"1.3516",
"entry_delay":1,
"technical":"bullish",
"rsi":"54",
"trend":"bullish",
"momentum":"0.018"
}

Rules:
confidence = integer 50-95
strength = integer 1-100
entry_delay = integer 0-3 only
rsi = visible value or "N/A"
momentum = visible/calculable value or "N/A"
technical = bullish, bearish or neutral
trend = bullish, bearish or neutral
"""


    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(
                data=image_bytes(image),
                mime_type="image/jpeg"
            ),
            prompt
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=250,
            response_mime_type="application/json"
        )
    )

    text = response.text.strip()

    if text.startswith("```"):

        text = (
            text
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

    return json.loads(text)


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(data):

    asset = str(
        data.get(
            "asset",
            "UNKNOWN"
        )
    )

    timeframe = str(
        data.get(
            "timeframe",
            "UNKNOWN"
        )
    )

    direction = str(
        data.get(
            "direction",
            "UP"
        )
    ).upper()

    confidence = data.get(
        "confidence",
        0
    )

    strength = data.get(
        "strength",
        0
    )

    entry_price = str(
        data.get(
            "entry_price",
            "UNKNOWN"
        )
    )

    delay = data.get(
        "entry_delay",
        1
    )

    technical = str(
        data.get(
            "technical",
            "N/A"
        )
    )

    rsi = str(
        data.get(
            "rsi",
            "N/A"
        )
    )

    trend = str(
        data.get(
            "trend",
            "N/A"
        )
    )

    momentum = str(
        data.get(
            "momentum",
            "N/A"
        )
    )

    try:
        delay = int(delay)
    except:
        delay = 1

    delay = max(
        0,
        min(delay, 3)
    )

    if delay == 0:

        entry_time = (
            "بداية الشمعة القادمة"
        )

    elif delay == 1:

        entry_time = (
            "بعد 1 دقيقة"
        )

    else:

        entry_time = (
            f"بعد {delay} دقائق"
        )

    if direction == "DOWN":

        emoji = "🔴"

    else:

        emoji = "🟢"
        direction = "UP"

    return (
        "🎓 تحليل زينو\n\n"

        f"🎯 القرار: {emoji} {direction}\n"
        f"📊 Confidence: {confidence}%\n"
        f"💪 Strength: {strength}%\n"

        "━━━━━━━━━━━━━━\n\n"

        f"📊 {asset} · ⏱ {timeframe}\n\n"

        f"💰 سعر الدخول: {entry_price}\n"
        f"🕐 وقت الدخول: {entry_time}\n"

        "━━━━━━━━━━━━━━\n\n"

        f"📈 Technical: {technical}\n"
        f"📊 RSI: {rsi}\n"
        f"📊 Trend: {trend}\n"
        f"📊 Momentum: {momentum}"
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_owner(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "⛔ هذا البوت خاص."
        )
        return

    if not update.message:
        return

    if not update.message.photo:
        return

    status = await update.message.reply_text(
        "⚡ تحليل سريع..."
    )

    try:

        photo = update.message.photo[-1]

        telegram_file = (
            await context.bot.get_file(
                photo.file_id
            )
        )

        image_data = (
            await telegram_file.download_as_bytearray()
        )

        image = Image.open(
            io.BytesIO(image_data)
        )

        result = await analyze_chart(
            image
        )

        message = format_signal(
            result
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ WIN",
                        callback_data="WIN"
                    ),
                    InlineKeyboardButton(
                        "❌ LOSS",
                        callback_data="LOSS"
                    )
                ]
            ]
        )

        await status.edit_text(
            message,
            reply_markup=keyboard
        )

    except json.JSONDecodeError:

        await status.edit_text(
            "❌ تعذر قراءة نتيجة التحليل.\n"
            "أعد إرسال الشارت."
        )

    except Exception as e:

        error = str(e)

        if len(error) > 600:
            error = error[:600]

        await status.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{error}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "stats",
            show_stats
        )
    )

    app.add_handler(
        CommandHandler(
            "reset",
            reset_stats
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            result_button
        )
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo
        )
    )

    print(
        "Zino Fast Signal AI started."
    )

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
