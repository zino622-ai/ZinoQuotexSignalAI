import os
import io
import json
import logging
import threading
import asyncio
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID_RAW:
    raise RuntimeError("OWNER_ID is missing")


try:
    OWNER_ID = int(OWNER_ID_RAW)
except ValueError as exc:
    raise RuntimeError(
        "OWNER_ID must be an integer"
    ) from exc


try:
    PORT = int(PORT_RAW)
except ValueError:
    PORT = 10000


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
# TIMEZONE
# =========================================================

UTC_MINUS_3 = timezone(
    timedelta(hours=-3)
)


# =========================================================
# STATS
# =========================================================

stats = {
    "win": 0,
    "loss": 0,
}


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(update: Update) -> bool:

    user = update.effective_user

    return bool(
        user
        and user.id == OWNER_ID
    )


async def reject_non_owner(
    update: Update,
) -> None:

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
                    "✅ WIN",
                    callback_data="win",
                ),
                InlineKeyboardButton(
                    "❌ LOSS",
                    callback_data="loss",
                ),
            ]
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
        "سيتم التحليل مباشرة.\n\n"

        "🕐 وقت الدخول يظهر بتوقيت UTC-3.",

        reply_markup=main_keyboard(),
    )


# =========================================================
# JSON EXTRACTION
# =========================================================

def extract_json(
    text: str,
) -> dict:

    text = text.strip()

    if text.startswith("```"):

        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if (
            lines
            and lines[-1].strip() == "```"
        ):

            lines = lines[:-1]

        text = "\n".join(lines).strip()


    start = text.find("{")
    end = text.rfind("}")


    if start == -1 or end == -1:

        raise ValueError(
            "Gemini did not return valid JSON"
        )


    return json.loads(
        text[start:end + 1]
    )


# =========================================================
# CHART ANALYSIS
# =========================================================

def analyze_chart(
    image_bytes: bytes,
) -> dict:

    prompt = r"""
You are a simple BotTrader-style chart analysis assistant
for Quotex.

Analyze ONLY the current screenshot.

Every screenshot is a completely new analysis.

Never use:
- previous screenshots
- previous signals
- previous analysis
- cached results
- old directions

Analyze the current screenshot independently.

Read only information that is actually visible.

Identify:

1. Asset / currency pair.
2. Visible chart timeframe.
3. Moving average direction only if a moving average is visible.
4. Technical-indicator direction only from indicators actually visible.
5. Recent candle direction.
6. Recent price action.
7. Market structure.
8. Breakout or rejection only when clearly visible.

Keep the analysis simple.

Do NOT invent indicators.

Do NOT mention RSI unless RSI is actually visible.

Do NOT mention MACD unless MACD is actually visible.

Do NOT mention Bollinger Bands unless they are actually visible.

Do NOT mention Keltner Channel unless it is actually visible.

Do NOT mention ADX unless it is actually visible.

Do NOT mention Support/Resistance unless clearly visible.

Do NOT use hidden or imaginary indicators.

Do NOT create information that cannot be read from the screenshot.

The final signal MUST be exactly:

STRONG BUY

or

STRONG SELL

Never return NO SIGNAL.

Do not alternate BUY and SELL artificially.

Choose the direction from the strongest visible evidence
in THIS screenshot.

CONFIDENCE:

Return an integer from 50 to 99.

Confidence is an analysis-confidence score.
It is NOT a guaranteed win probability.

Do not inflate the confidence without strong visible evidence.


IMPORTANT ENTRY TIME RULE:

Do NOT return the time when the screenshot was sent.

Determine the appropriate ENTRY DELAY from the current
chart analysis.

Choose ONLY:

1 minute

or

2 minutes

Use 1 minute when the setup is already sufficiently
confirmed for the next candle.

Use 2 minutes when an additional candle confirmation
is more appropriate.

Return entry_delay as an integer:

1

or

2


Return ONLY valid JSON.

Use exactly this structure:

{
  "asset": "USD/JPY",
  "timeframe": "5 MIN",
  "broker": "Quotex",
  "moving_average": "Sell",
  "technical_indicators": "Strong Sell",
  "signal": "STRONG SELL",
  "confidence": 82,
  "entry_delay": 2
}

Rules:

asset:
Read the visible pair as accurately as possible.

timeframe:
Read the visible chart timeframe.

broker:
Always return "Quotex".

moving_average:
Return only:
"Buy"
"Sell"
"Not Visible"

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

confidence:
Integer from 50 to 99.

entry_delay:
Integer 1 or 2.
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


    data = extract_json(
        response.text
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

        raise ValueError(
            "Gemini returned an invalid signal"
        )


    try:

        confidence = int(
            data.get("confidence")
        )

        entry_delay = int(
            data.get("entry_delay")
        )

    except (
        TypeError,
        ValueError,
    ):

        raise ValueError(
            "Gemini returned invalid confidence or entry delay"
        )


    if entry_delay not in (
        1,
        2,
    ):

        raise ValueError(
            "Gemini returned an invalid entry delay"
        )


    data["confidence"] = max(
        50,
        min(
            99,
            confidence,
        ),
    )

    data["entry_delay"] = entry_delay


    return data


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(
    data: dict,
) -> str:

    asset = str(
        data.get(
            "asset",
            "Unknown",
        )
    ).strip()


    timeframe = str(
        data.get(
            "timeframe",
            "Unknown",
        )
    ).strip()


    broker = str(
        data.get(
            "broker",
            "Quotex",
        )
    ).strip()


    moving_average = str(
        data.get(
            "moving_average",
            "Not Visible",
        )
    ).strip()


    technical_indicators = str(
        data.get(
            "technical_indicators",
            "Not Visible",
        )
    ).strip()


    signal = str(
        data.get(
            "signal",
            "",
        )
    ).upper().strip()


    confidence = int(
        data.get(
            "confidence",
            50,
        )
    )


    entry_delay = int(
        data.get(
            "entry_delay",
            1,
        )
    )


    # حساب وقت الدخول الحقيقي
    entry_time = (
        datetime.now(
            UTC_MINUS_3
        )
        + timedelta(
            minutes=entry_delay
        )
    )


    entry_time_text = entry_time.strftime(
        "%H:%M:%S"
    )


    if signal == "STRONG BUY":

        emoji = "🟢"

    else:

        emoji = "🔴"


    return (

        f"📊 {asset}\n"

        f"⏱ Chart Time: {timeframe}\n\n"

        f"📈 Moving average: "
        f"{moving_average}\n"

        f"📊 Technical indicators: "
        f"{technical_indicators}\n\n"

        f"🎯 Trading signal: "
        f"{emoji} {signal}\n"

        f"📊 Confidence: "
        f"{confidence}%\n\n"

        f"🕐 وقت الدخول: "
        f"{entry_time_text} UTC-3\n\n"

        f"🏦 Broker: {broker}"
    )


# =========================================================
# RECEIVE SCREENSHOT
# =========================================================

async def receive_chart(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not is_owner(update):

        await reject_non_owner(update)

        return


    if (
        not update.message
        or not update.message.photo
    ):

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


    image_data = image_bytes.getvalue()


    # التحليل يبدأ مباشرة
    status_message = await update.message.reply_text(
        "🔎 جاري تحليل الشارت مباشرة..."
    )


    try:

        data = await asyncio.to_thread(
            analyze_chart,
            image_data,
        )


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

            f"{exc}\n\n"

            "📸 أرسل لقطة الشاشة مرة أخرى.",

            reply_markup=main_keyboard(),
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

        f"🎯 WIN RATE: "
        f"{win_rate:.1f}%"
    )


# =========================================================
# WIN / LOSS
# =========================================================

async def button_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not is_owner(update):

        await reject_non_owner(update)

        return


    query = update.callback_query


    await query.answer()


    if query.data == "win":

        stats["win"] += 1


        await query.message.reply_text(

            "✅ تم تسجيل WIN.\n\n"

            + format_stats(),

            reply_markup=main_keyboard(),
        )


        return


    if query.data == "loss":

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

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        body = (
            b"ZinoQuotexSignalAI is running"
        )


        self.send_response(200)


        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )


        self.send_header(
            "Content-Length",
            str(len(body)),
        )


        self.end_headers()


        self.wfile.write(body)


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
