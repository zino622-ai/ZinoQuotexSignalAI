import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from flask import Flask
from threading import Thread
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


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_RAW = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID_RAW:
    raise RuntimeError("OWNER_ID is missing")

try:
    OWNER_ID = int(OWNER_ID_RAW)
except ValueError:
    raise RuntimeError("OWNER_ID must be an integer")


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# GEMINI
# =========================================================

client = genai.Client(api_key=GEMINI_API_KEY)


# =========================================================
# STATS
# =========================================================

wins = 0
losses = 0

last_signal = None


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "ZinoQuotexSignalAI is running", 200


@app.route("/health")
def health():
    return "OK", 200


def run_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(update: Update) -> bool:
    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


async def deny(update: Update):
    if update.message:
        await update.message.reply_text("⛔ هذا البوت خاص بالمالك فقط.")


# =========================================================
# TIME
# =========================================================

UTC_MINUS_3 = timezone(timedelta(hours=-3))


def get_entry_time(delay_minutes: int = 1) -> str:
    now = datetime.now(UTC_MINUS_3)

    entry = now + timedelta(minutes=delay_minutes)

    entry = entry.replace(second=0, microsecond=0)

    return entry.strftime("%H:%M")


# =========================================================
# GEMINI PROMPT
# =========================================================

SYSTEM_PROMPT = r"""
You are Zino, a fast and disciplined Quotex chart analyst.

The user sends a screenshot of a Quotex trading chart.

Analyze ONLY what is actually visible in the screenshot.

IMPORTANT:
- Always return a direction.
- Never return NO SIGNAL.
- Direction must be exactly UP or DOWN.
- UP means CALL.
- DOWN means PUT.
- Do not randomly choose direction.
- Do not make every signal UP.
- Balance UP and DOWN according to the chart.
- Do not invent indicators that are not visible.
- Do not invent prices.
- Preserve the visible asset/pair.
- Detect the visible timeframe.
- Main priority:
  1. Candle open/close behavior
  2. Recent highs and lows
  3. Breakouts and confirmed candle closes
  4. Market structure
  5. Price Action
  6. Momentum
  7. Confirmation candle
  8. Keltner Channel if visible
  9. ADX / DI if visible

PRICE ACTION:
- Look at the last closed candle, not an unfinished candle.
- Pay attention to strong bullish/bearish closes.
- Look for rejection wicks.
- Look for engulfing candles.
- Look for hammer / pin-bar behavior.
- Look for breakouts confirmed by candle close.
- Avoid treating a wick alone as a confirmed breakout.

MARKET STRUCTURE:
- Higher highs + higher lows generally support UP.
- Lower highs + lower lows generally support DOWN.
- Consolidation should be recognized as consolidation.
- Near an important level, wait for confirmation from candle behavior.

KELTNER:
- If visible, use the Keltner Channel as confirmation.
- Upper-band rejection can support DOWN.
- Lower-band rejection can support UP.
- Strong closes outside the channel can support continuation only when price action confirms it.

ADX:
- If visible, use ADX/DI only as supporting confirmation.
- Positive DI can support UP.
- Negative DI can support DOWN.
- Do not let ADX alone decide the signal.

ENTRY:
- The entry should normally be at the beginning of the next suitable candle.
- The entry delay can be 1 or 2 minutes depending on the chart.
- Prefer 1 minute when the next candle is suitable.
- Use 2 minutes when waiting for confirmation is more appropriate.
- Do not use very long delays.
- Never give an entry delay of many minutes or hours.

CANCELLATION:
- Give a cancellation price directly below the entry price.
- The cancellation condition must be based on a meaningful nearby price level.
- For UP, cancellation should normally be if a candle closes below the selected invalidation level.
- For DOWN, cancellation should normally be if a candle closes above the selected invalidation level.
- Do not use absurdly distant levels.
- Do not output excessive decimal digits.
- Use exactly 6 digits after the decimal when possible.
- Example: 14.868390
- The cancellation price must be usable on a Quotex chart.

CONFIDENCE:
- Confidence must be realistic.
- Do not automatically use 80%, 90% or 95%.
- Strong confirmed setups can receive higher confidence.
- Weak setups should receive lower confidence.
- The confidence should reflect the actual visible evidence.

TIMEFRAME:
- Read the timeframe from the screenshot when possible.
- The user's Quotex chart may be 1M or 2M.
- Do not force the timeframe to 2M if the screenshot clearly shows another timeframe.

OUTPUT:
Return ONLY valid JSON.
No markdown.
No explanation outside JSON.

JSON schema:

{
  "asset": "USD/ZAR",
  "timeframe": "2M",
  "direction": "UP",
  "confidence": 72,
  "entry_delay": 2,
  "entry_price": "14.868390",
  "cancellation_price": "14.866500",
  "trend": "Bullish",
  "short_trend": "Bullish",
  "market_structure": "Higher highs and higher lows",
  "momentum": "Bullish",
  "price_action": "Bullish confirmation candle",
  "confirmation": "Bullish candle close",
  "reason": "Price action and structure support continuation upward.",
  "cancellation_condition": "إذا أغلقت شمعة تحت 14.866500"
}

Rules:
- direction MUST be exactly "UP" or "DOWN".
- confidence MUST be an integer from 1 to 100.
- entry_delay MUST be 1 or 2.
- entry_price must be a string.
- cancellation_price must be a string.
- cancellation_condition must match the direction.
- Keep reason short.
- JSON must be valid.
"""


# =========================================================
# JSON CLEANER
# =========================================================

def clean_json(text: str) -> dict:
    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("Gemini did not return JSON")

    text = text[start:end + 1]

    return json.loads(text)


# =========================================================
# ANALYSIS
# =========================================================

async def analyze_chart(image_bytes: bytes) -> dict:
    prompt = """
Analyze this Quotex chart screenshot according to the system instructions.

Return ONLY valid JSON.

Make the signal actionable:
- UP or DOWN
- confidence
- asset
- timeframe
- entry delay 1 or 2 minutes
- entry price
- cancellation price
- short technical reasoning

Do not return NO SIGNAL.
"""

    response = await asyncio.wait_for(
        asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg",
                ),
                prompt,
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.15,
                response_mime_type="application/json",
            ),
        ),
        timeout=25,
    )

    if not response or not response.text:
        raise ValueError("Empty Gemini response")

    result = clean_json(response.text)

    return result


# =========================================================
# FORMAT PRICE
# =========================================================

def format_price(value) -> str:
    try:
        number = float(value)

        # Quotex-friendly 6 decimal places
        return f"{number:.6f}"

    except Exception:
        return str(value)


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(data: dict) -> str:
    global last_signal

    asset = str(data.get("asset", "UNKNOWN"))
    timeframe = str(data.get("timeframe", "2M"))

    direction = str(data.get("direction", "UP")).upper()

    if direction not in ("UP", "DOWN"):
        direction = "UP"

    confidence = int(data.get("confidence", 50))

    try:
        confidence = max(1, min(100, confidence))
    except Exception:
        confidence = 50

    try:
        delay = int(data.get("entry_delay", 1))
    except Exception:
        delay = 1

    if delay not in (1, 2):
        delay = 1

    entry_price = format_price(data.get("entry_price", "0"))
    cancellation_price = format_price(
        data.get("cancellation_price", "0")
    )

    trend = str(data.get("trend", "N/A"))
    short_trend = str(data.get("short_trend", "N/A"))
    structure = str(data.get("market_structure", "N/A"))
    momentum = str(data.get("momentum", "N/A"))
    price_action = str(data.get("price_action", "N/A"))
    confirmation = str(data.get("confirmation", "N/A"))
    reason = str(data.get("reason", "N/A"))

    entry_time = get_entry_time(delay)

    if direction == "UP":
        direction_text = "🟢 UP — CALL"
        cancellation_text = (
            f"🛑 إلغاء إذا أغلقت شمعة تحت {cancellation_price}"
        )
    else:
        direction_text = "🔴 DOWN — PUT"
        cancellation_text = (
            f"🛑 إلغاء إذا أغلقت شمعة فوق {cancellation_price}"
        )

    signal = (
        "🎓 تحليل زينو\n\n"
        f"🎯 Confidence: {confidence}%\n"
        f"📊 {asset} · ⏱ {timeframe}\n"
        "━━━━━━━━━━━━━━\n\n"
        f"🎯 القرار: {direction_text}\n"
        f"🕐 الدخول بعد: {delay} دقيقة\n"
        f"⏰ وقت الدخول: {entry_time} UTC-3\n\n"
        f"💵 سعر الدخول: {entry_price}\n"
        f"{cancellation_text}\n\n"
        "📈 التحليل\n"
        f"• الاتجاه: {trend}\n"
        f"• الاتجاه القصير: {short_trend}\n"
        f"• Market Structure: {structure}\n"
        f"• Momentum: {momentum}\n"
        f"• Price Action: {price_action}\n"
        f"• Confirmation: {confirmation}\n\n"
        f"🧠 السبب: {reason}"
    )

    last_signal = {
        "asset": asset,
        "timeframe": timeframe,
        "direction": direction,
        "confidence": confidence,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "cancellation_price": cancellation_price,
    }

    return signal


# =========================================================
# WIN / LOSS KEYBOARD
# =========================================================

def result_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("✅ WIN", callback_data="result_win"),
            InlineKeyboardButton("❌ LOSS", callback_data="result_loss"),
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await deny(update)
        return

    await update.message.reply_text(
        "🎓 ZinoQuotexSignalAI\n\n"
        "📸 أرسل Screenshot للشارت وسأحلله.\n\n"
        "✅ /win — تسجيل صفقة رابحة\n"
        "❌ /loss — تسجيل صفقة خاسرة\n"
        "📊 /stats — الإحصائيات"
    )


# =========================================================
# PHOTO HANDLER
# =========================================================

async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update):
        await deny(update)
        return

    message = update.message

    if not message or not message.photo:
        return

    status = await message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:
        photo = message.photo[-1]

        file = await context.bot.get_file(photo.file_id)

        image_bytes = await file.download_as_bytearray()

        result = await analyze_chart(bytes(image_bytes))

        signal_text = format_signal(result)

        await status.edit_text(
            signal_text,
            reply_markup=result_keyboard(),
        )

    except asyncio.TimeoutError:
        await status.edit_text(
            "❌ انتهى وقت التحليل. أرسل الصورة مرة أخرى."
        )

    except Exception as e:
        logger.exception("Analysis error")

        await status.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{str(e)[:1000]}"
        )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    global wins, losses

    query = update.callback_query

    if not query:
        return

    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ غير مصرح", show_alert=True)
        return

    await query.answer()

    if query.data == "result_win":
        wins += 1

        await query.edit_message_reply_markup(
            reply_markup=None
        )

        await query.message.reply_text(
            "✅ تم تسجيل WIN\n\n"
            f"🏆 WIN: {wins}\n"
            f"❌ LOSS: {losses}\n"
            f"📊 المجموع: {wins + losses}\n"
            f"🎯 نسبة النجاح: {win_rate()}%"
        )

    elif query.data == "result_loss":
        losses += 1

        await query.edit_message_reply_markup(
            reply_markup=None
        )

        await query.message.reply_text(
            "❌ تم تسجيل LOSS\n\n"
            f"🏆 WIN: {wins}\n"
            f"❌ LOSS: {losses}\n"
            f"📊 المجموع: {wins + losses}\n"
            f"🎯 نسبة النجاح: {win_rate()}%"
        )


# =========================================================
# WIN RATE
# =========================================================

def win_rate():
    total = wins + losses

    if total == 0:
        return 0.0

    return round((wins / total) * 100, 2)


# =========================================================
# /WIN
# =========================================================

async def win_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    global wins

    if not is_owner(update):
        await deny(update)
        return

    wins += 1

    await update.message.reply_text(
        "✅ WIN مسجلة\n\n"
        f"🏆 WIN: {wins}\n"
        f"❌ LOSS: {losses}\n"
        f"📊 المجموع: {wins + losses}\n"
        f"🎯 نسبة النجاح: {win_rate()}%"
    )


# =========================================================
# /LOSS
# =========================================================

async def loss_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    global losses

    if not is_owner(update):
        await deny(update)
        return

    losses += 1

    await update.message.reply_text(
        "❌ LOSS مسجلة\n\n"
        f"🏆 WIN: {wins}\n"
        f"❌ LOSS: {losses}\n"
        f"📊 المجموع: {wins + losses}\n"
        f"🎯 نسبة النجاح: {win_rate()}%"
    )


# =========================================================
# /STATS
# =========================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update):
        await deny(update)
        return

    total = wins + losses

    await update.message.reply_text(
        "📊 إحصائيات زينو\n\n"
        f"🏆 WIN: {wins}\n"
        f"❌ LOSS: {losses}\n"
        f"📈 المجموع: {total}\n"
        f"🎯 نسبة النجاح: {win_rate()}%"
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception(
        "Telegram error",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():
    Thread(
        target=run_server,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("win", win_command)
    )

    application.add_handler(
        CommandHandler("loss", loss_command)
    )

    application.add_handler(
        CommandHandler("stats", stats_command)
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "ZinoQuotexSignalAI started | model=%s",
        GEMINI_MODEL,
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
