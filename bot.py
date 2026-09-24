import os
import io
import json
import logging
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
    "gemini-3.5-flash-lite"
)

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
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("ZinoAI")


# =========================================================
# GEMINI
# =========================================================

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# MEMORY
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

    return bool(
        user and user.id == OWNER_ID
    )


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


# =========================================================
# KEYBOARDS
# =========================================================

def signal_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎯 Get Signal",
                callback_data="get_signal"
            )
        ]
    ])


def result_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ WIN",
                callback_data="result_win"
            ),
            InlineKeyboardButton(
                "❌ LOSS",
                callback_data="result_loss"
            ),
        ],
        [
            InlineKeyboardButton(
                "🎯 Get Signal",
                callback_data="get_signal"
            )
        ]
    ])


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if await reject_non_owner(update):
        return

    await update.message.reply_text(
        "🤖 ZINO BOTTRADER AI\n\n"
        "📸 أرسل Screenshot للشارت.\n"
        "بعدها اضغط على Get Signal.",
        reply_markup=signal_keyboard(),
    )


# =========================================================
# RECEIVE SCREENSHOT
# =========================================================

async def receive_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global latest_chart

    if await reject_non_owner(update):
        return

    photo = update.message.photo[-1]

    file = await context.bot.get_file(
        photo.file_id
    )

    buffer = io.BytesIO()

    await file.download_to_memory(
        buffer
    )

    latest_chart = buffer.getvalue()

    await update.message.reply_text(
        "📸 Chart received.\n\n"
        "اضغط Get Signal لتحليل الشارت.",
        reply_markup=signal_keyboard(),
    )


# =========================================================
# BOTTRADER ANALYSIS PROMPT
# =========================================================

ANALYSIS_PROMPT = r"""
You are a professional chart-analysis AI working in the style of
a simple Quotex BotTrader signal system.

Analyze ONLY the screenshot provided by the user.

The output must follow this exact analytical structure:

1. Asset / currency pair
2. Timeframe
3. Broker
4. Moving average
5. Technical indicators
6. Final trading signal

Example:

USD/JPY
Time: 5 MIN
Broker: Quotex
Moving average: Sell
Technical indicators: Strong Sell
Trading signal from bot: STRONG SELL

IMPORTANT ANALYSIS RULES:

- Read the asset/pair from the screenshot.
- Read the visible timeframe from the screenshot.
- Broker is Quotex unless the screenshot clearly shows another broker.
- Analyze the visible moving-average direction.
- Analyze the visible technical-indicator direction.
- Determine whether the combined evidence is BUY or SELL.
- The final signal must be either:
  STRONG BUY
  or
  STRONG SELL
- Do NOT return NO_SIGNAL.
- Always provide one final direction.
- Do NOT invent exact indicator numbers that cannot be seen.
- Do NOT invent prices.
- Do NOT invent a timeframe.
- Do NOT claim guaranteed profit or guaranteed accuracy.
- Do NOT add Keltner Channel.
- Do NOT add ADX.
- Do NOT add RSI unless it is visibly present and relevant to the
  technical-indicator assessment.
- Do NOT add MACD unless it is visibly present and relevant.
- Do NOT add Bollinger Bands unless they are visibly present.
- Do NOT add support/resistance levels.
- Do NOT add entry price.
- Do NOT add expiry.
- Do NOT add extra indicators that are not visible.
- Focus on the same simple structure as the BotTrader example.

MOVING AVERAGE:

Classify the visible moving-average bias as one of:

Buy
Sell
Neutral

If the chart clearly shows the price moving above the relevant
moving-average structure, this supports Buy.

If the chart clearly shows the price moving below the relevant
moving-average structure, this supports Sell.

If the visible moving-average information is mixed, use the
overall visible direction rather than inventing a value.

TECHNICAL INDICATORS:

Classify the overall visible technical-indicator bias as:

Strong Buy
Buy
Neutral
Sell
Strong Sell

Use the visible evidence from the screenshot.

FINAL SIGNAL:

Combine the Moving Average and Technical Indicators.

If both support buying:
STRONG BUY

If both support selling:
STRONG SELL

If they are mixed:
choose the direction supported by the stronger visible
price-action/indicator evidence.

Never return NO_SIGNAL.

Do not use markdown.

Return ONLY valid JSON.

Use exactly these keys:

{
  "asset": "USD/JPY",
  "timeframe": "5 MIN",
  "broker": "Quotex",
  "moving_average": "Sell",
  "technical_indicators": "Strong Sell",
  "trading_signal": "STRONG SELL"
}
"""


# =========================================================
# CLEAN JSON
# =========================================================

def clean_json(text: str):

    text = text.strip()

    if text.startswith("```"):

        text = text.replace(
            "```json",
            "",
            1
        )

        text = text.replace(
            "```",
            ""
        )

        text = text.strip()

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
# ANALYZE CHART
# =========================================================

async def analyze_chart():

    if not latest_chart:
        raise ValueError(
            "No chart screenshot available"
        )

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

    if not response.text:
        raise ValueError(
            "Gemini returned an empty response"
        )

    data = clean_json(
        response.text
    )

    asset = str(
        data.get(
            "asset",
            "Unknown"
        )
    ).strip()

    timeframe = str(
        data.get(
            "timeframe",
            "Unknown"
        )
    ).strip()

    broker = str(
        data.get(
            "broker",
            "Quotex"
        )
    ).strip()

    moving_average = str(
        data.get(
            "moving_average",
            ""
        )
    ).strip()

    technical_indicators = str(
        data.get(
            "technical_indicators",
            ""
        )
    ).strip()

    trading_signal = str(
        data.get(
            "trading_signal",
            ""
        )
    ).strip().upper()

    if not asset:
        asset = "Unknown"

    if not timeframe:
        timeframe = "Unknown"

    if not broker:
        broker = "Quotex"

    valid_ma = {
        "BUY",
        "SELL",
        "NEUTRAL",
    }

    valid_technical = {
        "STRONG BUY",
        "BUY",
        "NEUTRAL",
        "SELL",
        "STRONG SELL",
    }

    if moving_average.upper() not in valid_ma:

        raise ValueError(
            f"Invalid moving average result: "
            f"{moving_average}"
        )

    if technical_indicators.upper() not in valid_technical:

        raise ValueError(
            f"Invalid technical indicators result: "
            f"{technical_indicators}"
        )

    if trading_signal not in {
        "STRONG BUY",
        "STRONG SELL",
    }:

        raise ValueError(
            f"Invalid trading signal: "
            f"{trading_signal}"
        )

    return {
        "asset": asset,
        "timeframe": timeframe,
        "broker": broker,
        "moving_average": moving_average,
        "technical_indicators": technical_indicators,
        "trading_signal": trading_signal,
    }


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(data):

    signal = data["trading_signal"]

    if signal == "STRONG BUY":

        signal_display = "🟢 STRONG BUY"

    else:

        signal_display = "🔴 STRONG SELL"

    return (
        f"📊 {data['asset']}\n"
        f"⏱ Time: {data['timeframe']}\n"
        f"🏦 Broker: {data['broker']}\n\n"

        f"Moving average: "
        f"{data['moving_average']}\n"

        f"Technical indicators: "
        f"{data['technical_indicators']}\n\n"

        f"Trading signal from bot: "
        f"{signal_display}"
    )


# =========================================================
# GET SIGNAL
# =========================================================

async def get_signal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global last_signal

    query = update.callback_query

    if await reject_non_owner(update):
        return

    await query.answer()

    if not latest_chart:

        await query.message.reply_text(
            "📸 أرسل Screenshot للشارت أولاً.",
            reply_markup=signal_keyboard(),
        )

        return

    analyzing_message = await query.message.reply_text(
        "🔎 Analyzing chart..."
    )

    try:

        data = await analyze_chart()

        last_signal = data

        await analyzing_message.edit_text(
            format_signal(data)
        )

        await query.message.reply_text(
            "هل كانت النتيجة؟",
            reply_markup=result_keyboard(),
        )

    except Exception as exc:

        logger.exception(
            "Analysis error"
        )

        try:

            await analyzing_message.edit_text(
                f"❌ Analysis error:\n{exc}"
            )

        except Exception:

            await query.message.reply_text(
                f"❌ Analysis error:\n{exc}",
                reply_markup=signal_keyboard(),
            )


# =========================================================
# WIN / LOSS
# =========================================================

async def result_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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

    total = (
        stats["win"]
        +
        stats["loss"]
    )

    if total:

        win_rate = (
            stats["win"]
            /
            total
        ) * 100

    else:

        win_rate = 0

    await query.message.reply_text(

        f"{text}\n\n"
        f"WIN: {stats['win']}\n"
        f"LOSS: {stats['loss']}\n"
        f"Win Rate: {win_rate:.0f}%",

        reply_markup=signal_keyboard(),
    )


# =========================================================
# BUTTON ROUTER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query.data == "get_signal":

        await get_signal(
            update,
            context
        )

        return

    if query.data in {
        "result_win",
        "result_loss"
    }:

        await result_callback(
            update,
            context
        )

        return


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Unhandled exception",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            receive_photo
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Zino BotTrader AI is running"
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
