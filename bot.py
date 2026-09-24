import os
import io
import json
import logging
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_RAW = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite",
)

PORT_RAW = os.getenv("PORT", "10000")

try:
    PORT = int(PORT_RAW)
except ValueError:
    PORT = 10000


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID_RAW:
    raise RuntimeError("OWNER_ID is missing")

try:
    OWNER_ID = int(OWNER_ID_RAW)
except ValueError as exc:
    raise RuntimeError("OWNER_ID must be an integer") from exc


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("ZinoAI")


# =========================================================
# GEMINI
# =========================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# BOT STATE
# =========================================================

latest_chart = None
last_signal = None

stats = {
    "win": 0,
    "loss": 0,
}


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(update: Update) -> bool:
    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


async def reject_non_owner(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ هذا البوت خاص بالمالك فقط."
        )


# =========================================================
# KEYBOARD
# =========================================================

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎯 Get Signal",
                    callback_data="get_signal",
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ WIN",
                    callback_data="win",
                ),
                InlineKeyboardButton(
                    "❌ LOSS",
                    callback_data="loss",
                ),
            ],
        ]
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not is_owner(update):
        await reject_non_owner(update)
        return

    await update.message.reply_text(
        "🎓 ZinoQuotexSignalAI\n\n"
        "📸 أرسل Screenshot للشارت.\n"
        "ثم اضغط 🎯 Get Signal.",
        reply_markup=main_keyboard(),
    )


# =========================================================
# RECEIVE SCREENSHOT
# =========================================================

async def receive_chart(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    global latest_chart

    if not is_owner(update):
        await reject_non_owner(update)
        return

    if not update.message:
        return

    if not update.message.photo:
        return

    photo = update.message.photo[-1]

    telegram_file = await context.bot.get_file(
        photo.file_id
    )

    image_bytes = io.BytesIO()

    await telegram_file.download_to_memory(
        image_bytes
    )

    image_bytes.seek(0)

    latest_chart = image_bytes.getvalue()

    await update.message.reply_text(
        "📸 تم استلام الشارت.\n\n"
        "🎯 اضغط Get Signal للتحليل.",
        reply_markup=main_keyboard(),
    )


# =========================================================
# JSON EXTRACTION
# =========================================================

def extract_json(text: str) -> dict:

    text = text.strip()

    if text.startswith("```"):

        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(
            "Gemini did not return valid JSON"
        )

    json_text = text[start:end + 1]

    return json.loads(json_text)


# =========================================================
# CHART ANALYSIS
# =========================================================

def analyze_chart(image_bytes: bytes) -> dict:

    prompt = """
You are a simple BotTrader-style chart analysis assistant
for Quotex.

Analyze the screenshot carefully.

Use ONLY information that is actually visible in the chart.

Identify:

1. Currency pair / asset.
2. Visible timeframe.
3. Moving average direction if a moving average is visible.
4. Overall technical-indicator direction if visible.
5. Recent candle direction and price action.
6. Market structure and breakout/rejection when clearly visible.

IMPORTANT:

Keep the analysis simple.

Do NOT invent information.

Do NOT invent indicators that are not visible.

Do NOT add RSI unless RSI is actually visible.

Do NOT add MACD unless MACD is actually visible.

Do NOT add Bollinger Bands unless they are actually visible.

Do NOT add Keltner Channel unless it is actually visible.

Do NOT add ADX unless it is actually visible.

Do NOT add Support/Resistance unless clearly visible.

Do NOT use hidden or imaginary indicators.

The final signal MUST always be exactly one of:

STRONG BUY

or

STRONG SELL

Never return NO SIGNAL.

Do not alternate BUY and SELL artificially.

Do not use previous signals.

Analyze every screenshot independently.

Return ONLY valid JSON.

Use exactly this structure:

{
  "asset": "USD/JPY",
  "timeframe": "5 MIN",
  "broker": "Quotex",
  "moving_average": "Sell",
  "technical_indicators": "Strong Sell",
  "signal": "STRONG SELL"
}

Rules:

asset:
Read the visible pair as accurately as possible.

timeframe:
Read the visible chart timeframe.

broker:
Always return "Quotex".

moving_average:
Return "Buy", "Sell", or "Not Visible".

technical_indicators:
Return a concise description such as:
"Strong Buy"
"Buy"
"Strong Sell"
"Sell"
"Mixed"
"Not Visible"

signal:
Must be exactly:
"STRONG BUY"
or
"STRONG SELL"

Choose the direction from the strongest visible evidence
in the screenshot.
"""


    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg",
            ),
            prompt,
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )

    if not response.text:
        raise ValueError(
            "Gemini returned an empty response"
        )

    return extract_json(
        response.text
    )


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(data: dict) -> str:

    asset = str(
        data.get(
            "asset",
            "Unknown",
        )
    )

    timeframe = str(
        data.get(
            "timeframe",
            "Unknown",
        )
    )

    broker = str(
        data.get(
            "broker",
            "Quotex",
        )
    )

    moving_average = str(
        data.get(
            "moving_average",
            "Not Visible",
        )
    )

    technical_indicators = str(
        data.get(
            "technical_indicators",
            "Not Visible",
        )
    )

    signal = str(
        data.get(
            "signal",
            "",
        )
    ).upper().strip()

    if signal not in (
        "STRONG BUY",
        "STRONG SELL",
    ):
        signal = "STRONG BUY"

    if signal == "STRONG BUY":
        emoji = "🟢"
    else:
        emoji = "🔴"

    return (
        f"📊 {asset}\n"
        f"⏱ Time: {timeframe}\n"
        f"🏦 Broker: {broker}\n"
        f"📈 Moving average: {moving_average}\n"
        f"📊 Technical indicators: "
        f"{technical_indicators}\n"
        f"🎯 Trading signal from bot: "
        f"{emoji} {signal}"
    )


# =========================================================
# STATS
# =========================================================

def format_stats() -> str:

    wins = stats["win"]
    losses = stats["loss"]
    total = wins + losses

    if total > 0:
        win_rate = (
            wins / total
        ) * 100
    else:
        win_rate = 0

    return (
        "📊 إحصائيات الإشارات\n\n"
        f"✅ WIN: {wins}\n"
        f"❌ LOSS: {losses}\n"
        f"📌 TOTAL: {total}\n"
        f"🎯 WIN RATE: {win_rate:.1f}%"
    )


# =========================================================
# GET SIGNAL
# =========================================================

async def get_signal(
    update: Update,
) -> None:

    global last_signal

    if not is_owner(update):
        await reject_non_owner(update)
        return

    query = update.callback_query

    await query.answer()

    if latest_chart is None:

        await query.message.reply_text(
            "📸 أرسل Screenshot للشارت أولاً.",
            reply_markup=main_keyboard(),
        )

        return

    status_message = await query.message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:

        data = await asyncio.to_thread(
            analyze_chart,
            latest_chart,
        )

        last_signal = data

        await status_message.edit_text(
            format_signal(data),
            reply_markup=main_keyboard(),
        )

    except Exception as exc:

        logger.exception(
            "Analysis error"
        )

        await status_message.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{exc}",
            reply_markup=main_keyboard(),
        )


# =========================================================
# BUTTON ROUTER
# =========================================================

async def button_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not is_owner(update):
        await reject_non_owner(update)
        return

    query = update.callback_query

    data = query.data

    if data == "get_signal":

        await get_signal(update)

        return

    if data == "win":

        await query.answer(
            "WIN ✅"
        )

        stats["win"] += 1

        await query.message.reply_text(
            "✅ تم تسجيل WIN.\n\n"
            + format_stats(),
            reply_markup=main_keyboard(),
        )

        return

    if data == "loss":

        await query.answer(
            "LOSS ❌"
        )

        stats["loss"] += 1

        await query.message.reply_text(
            "❌ تم تسجيل LOSS.\n\n"
            + format_stats(),
            reply_markup=main_keyboard(),
        )

        return


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    logger.error(
        "Unhandled exception: %s",
        context.error,
        exc_info=context.error,
    )


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(
                len(
                    b"ZinoQuotexSignalAI is running"
                )
            ),
        )

        self.end_headers()

        self.wfile.write(
            b"ZinoQuotexSignalAI is running"
        )

    def do_HEAD(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.end_headers()

    def log_message(
        self,
        format,
        *args,
    ):
        return


def start_health_server() -> None:

    try:

        server = ThreadingHTTPServer(
            (
                "0.0.0.0",
                PORT,
            ),
            HealthHandler,
        )

        logger.info(
            "HEALTH SERVER READY - port %s",
            PORT,
        )

        server.serve_forever()

    except Exception:

        logger.exception(
            "Health server failed"
        )


# =========================================================
# MAIN
# =========================================================

def main() -> None:

    logger.info(
        "Starting ZinoQuotexSignalAI..."
    )

    logger.info(
        "Render PORT: %s",
        PORT,
    )

    logger.info(
        "Gemini model: %s",
        GEMINI_MODEL,
    )

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
        name="render-health",
    )

    health_thread.start()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            receive_chart,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_router,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Telegram bot polling started"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
