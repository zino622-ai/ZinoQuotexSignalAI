import os
import io
import json
import logging
from datetime import datetime, timezone

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

from google import genai
from google.genai import types


# =========================
# CONFIG
# =========================

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


# =========================
# LOGGING
# =========================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("ZinoAI")


# =========================
# GEMINI
# =========================

gemini = genai.Client(api_key=GEMINI_API_KEY)


# =========================
# SIMPLE MEMORY
# =========================

latest_chart = None
last_signal = None

stats = {
    "win": 0,
    "loss": 0,
}


# =========================
# OWNER CHECK
# =========================

def is_owner(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == OWNER_ID)


async def reject_non_owner(update: Update) -> bool:
    if is_owner(update):
        return False

    if update.callback_query:
        try:
            await update.callback_query.answer(
                "⛔ هذا البوت خاص بالمالك.",
                show_alert=True,
            )
        except Exception:
            pass
    elif update.effective_message:
        await update.effective_message.reply_text(
            "⛔ هذا البوت خاص بالمالك."
        )

    return True


# =========================
# UI
# =========================

def signal_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 Get Signal", callback_data="get_signal")
        ]
    ])


def result_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ WIN", callback_data="result_win"),
            InlineKeyboardButton("❌ LOSS", callback_data="result_loss"),
        ],
        [
            InlineKeyboardButton("🎯 Get Signal", callback_data="get_signal")
        ]
    ])


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await reject_non_owner(update):
        return

    await update.message.reply_text(
        "🤖 ZOYA AI STYLE\n\n"
        "📸 أرسل Screenshot للشارت.\n"
        "بعدها اضغط على Get Signal.",
        reply_markup=signal_keyboard(),
    )


# =========================
# IMAGE RECEIVER
# =========================

async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global latest_chart

    if await reject_non_owner(update):
        return

    photo = update.message.photo[-1]

    file = await context.bot.get_file(photo.file_id)

    buffer = io.BytesIO()
    await file.download_to_memory(buffer)

    latest_chart = buffer.getvalue()

    await update.message.reply_text(
        "📸 Chart received.\n\n"
        "اضغط Get Signal لتحليل الشارت.",
        reply_markup=signal_keyboard(),
    )


# =========================
# GEMINI ANALYSIS
# =========================

ANALYSIS_PROMPT = r"""
You are an AI binary-options chart signal analyzer.

Analyze ONLY the chart screenshot supplied by the user.

The desired behavior is the simple style of a high-confidence Quotex signal bot.

IMPORTANT:
- Do NOT invent information that cannot be read from the screenshot.
- Identify the visible trading asset/pair when possible.
- Identify the visible chart timeframe when possible.
- Analyze the visible price action and candle structure.
- Look for ONE clear, high-confidence short-term setup.
- Do not force a signal when the chart does not provide a sufficiently clear setup.
- If there is no high-confidence setup, return NO_SIGNAL.
- Do not add indicators that are not visible in the screenshot.
- Do not claim a guaranteed win.
- Confidence is an estimate, not a guarantee.
- The signal must be either UP or DOWN.
- Expiry should normally match the visible short-term chart context.
- Entry should be the next suitable entry point visible from the chart.
- Keep the final explanation short.

Return ONLY valid JSON with exactly these keys:

{
  "status": "SIGNAL" or "NO_SIGNAL",
  "asset": "string",
  "timeframe": "string",
  "direction": "UP" or "DOWN" or "",
  "confidence": 0,
  "expiry": "string",
  "entry": "string",
  "reason": "short string"
}

Rules:
- If status is NO_SIGNAL, direction must be "", confidence must be 0,
  and expiry/entry can be "".
- confidence must be an integer from 0 to 100.
- Do not use markdown.
- Do not wrap JSON in ```.

"""


def clean_json(text: str):
    text = text.strip()

    if text.startswith("```"):
        text = text.replace("```json", "", 1)
        text = text.replace("```", "")
        text = text.strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("Gemini did not return JSON")

    return json.loads(text[start:end + 1])


async def analyze_chart():
    if not latest_chart:
        raise ValueError("No chart screenshot available")

    response = gemini.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(
                data=latest_chart,
                mime_type="image/jpeg",
            ),
            ANALYSIS_PROMPT,
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )

    data = clean_json(response.text)

    status = str(data.get("status", "")).upper()

    if status not in {"SIGNAL", "NO_SIGNAL"}:
        raise ValueError("Invalid signal status")

    if status == "NO_SIGNAL":
        return {
            "status": "NO_SIGNAL",
            "asset": data.get("asset", ""),
            "timeframe": data.get("timeframe", ""),
            "direction": "",
            "confidence": 0,
            "expiry": "",
            "entry": "",
            "reason": "",
        }

    direction = str(data.get("direction", "")).upper()

    if direction not in {"UP", "DOWN"}:
        raise ValueError("Invalid direction")

    confidence = int(data.get("confidence", 0))

    if confidence < 0 or confidence > 100:
        raise ValueError("Invalid confidence")

    return {
        "status": "SIGNAL",
        "asset": str(data.get("asset", "Unknown")),
        "timeframe": str(data.get("timeframe", "Unknown")),
        "direction": direction,
        "confidence": confidence,
        "expiry": str(data.get("expiry", "")),
        "entry": str(data.get("entry", "")),
        "reason": str(data.get("reason", "")),
    }


# =========================
# FORMAT
# =========================

def format_signal(data):
    direction = data["direction"]

    if direction == "UP":
        action = "🟢 BUY / UP"
    else:
        action = "🔴 SELL / DOWN"

    return (
        "🎯 ZOYA AI SIGNAL\n\n"
        f"{action}\n"
        f"📊 {data['asset']} · ⏱ {data['timeframe']}\n\n"
        f"🎯 Confidence: {data['confidence']}%\n"
        f"⏳ Expiry: {data['expiry']}\n"
        f"🕐 Entry: {data['entry']}\n\n"
        f"📌 {data['reason']}"
    )


def format_no_signal(data):
    return (
        "❌ No high-confidence setup is available right now.\n\n"
        "Please try again shortly."
    )


# =========================
# GET SIGNAL
# =========================

async def get_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_signal

    query = update.callback_query

    if await reject_non_owner(update):
        return

    await query.answer()

    if not latest_chart:
        await query.message.reply_text(
            "📸 أرسل Screenshot للشارت أولاً."
        )
        return

    await query.message.reply_text("🔎 Analyzing chart...")

    try:
        data = await analyze_chart()
        last_signal = data

        if data["status"] == "NO_SIGNAL":
            await query.message.reply_text(
                format_no_signal(data),
                reply_markup=signal_keyboard(),
            )
            return

        await query.message.reply_text(
            format_signal(data),
            reply_markup=result_keyboard(),
        )

    except Exception as exc:
        logger.exception("Analysis error")

        await query.message.reply_text(
            f"❌ Analysis error:\n{exc}",
            reply_markup=signal_keyboard(),
        )


# =========================
# WIN / LOSS
# =========================

async def result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if await reject_non_owner(update):
        return

    await query.answer()

    if query.data == "result_win":
        stats["win"] += 1
        text = "✅ WIN recorded."

    else:
        stats["loss"] += 1
        text = "❌ LOSS recorded."

    total = stats["win"] + stats["loss"]

    if total:
        win_rate = (stats["win"] / total) * 100
    else:
        win_rate = 0

    await query.message.reply_text(
        f"{text}\n\n"
        f"WIN: {stats['win']}\n"
        f"LOSS: {stats['loss']}\n"
        f"Win Rate: {win_rate:.0f}%",
        reply_markup=signal_keyboard(),
    )


# =========================
# BUTTON ROUTER
# =========================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "get_signal":
        await get_signal(update, context)
        return

    if query.data in {"result_win", "result_loss"}:
        await result_callback(update, context)
        return


# =========================
# ERROR HANDLER
# =========================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception(
        "Unhandled exception",
        exc_info=context.error,
    )


# =========================
# MAIN
# =========================

def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            receive_photo,
        )
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_error_handler(error_handler)

    logger.info("Zoya-style Zino AI bot is running")

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
