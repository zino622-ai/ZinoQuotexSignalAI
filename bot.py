import os
import io
import json
import logging
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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
OWNER_ID = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

OWNER_ID = int(OWNER_ID)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

gemini = genai.Client(api_key=GEMINI_API_KEY)


# =========================================================
# TIME
# =========================================================

UTC_MINUS_3 = timezone(timedelta(hours=-3))


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(update: Update):
    if not update.effective_user:
        return False

    return update.effective_user.id == OWNER_ID


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_owner(update):
        await update.message.reply_text(
            "⛔ هذا البوت خاص."
        )
        return

    await update.message.reply_text(
        "🎓 ZinoProSignalAI\n\n"
        "📸 أرسل Screenshot للشارت.\n"
        "⚡ سأقوم بتحليله وإعطائك الإشارة."
    )


# =========================================================
# ANALYSIS PROMPT
# =========================================================

ANALYSIS_PROMPT = """
أنت محرك التحليل الفني الرئيسي لبوت ZinoProSignalAI.

حلل Screenshot الخاصة بشارت Quotex بدقة.

المطلوب استخراج:
- اسم الزوج
- الفريم
- السعر الحالي
- اتجاه السوق
- الشموع
- البنية السعرية
- أي مؤشرات ظاهرة بالصورة

ثم قيّم الاتجاهين UP و DOWN بشكل منفصل.

نظام النقاط:

Structure       = 2 نقاط
Breakout        = 2 نقاط
Liquidity       = 1 نقطة
Momentum        = 2 نقاط
Candle          = 2 نقاط
RSI             = 1 نقطة
Summary         = 2 نقاط
Oscillators     = 2 نقاط
Moving Averages = 2 نقاط

المجموع = 18 نقطة.

مهم جداً:
لا تجعل UP هو الاتجاه الافتراضي.
ولا تجعل DOWN هو الاتجاه الافتراضي.

كل اتجاه يجب أن يحصل على نقاطه بناءً على الأدلة الموجودة في الصورة.

بعد التحليل اختر الاتجاه الذي لديه أفضل توافق فني.

حدد:
- UP أو DOWN
- مجموع نقاط الاتجاه المختار
- مجموع نقاط الاتجاه المقابل
- Confidence من 0 إلى 100
- Entry بعد 0 أو 1 أو 2 دقيقة حسب وضع السوق
- سعر الدخول
- مستوى إلغاء منطقي

سعر الإلغاء يجب أن يكون أسفل الدخول في UP،
وفوق الدخول في DOWN.

لا تخترع أسعاراً غير ظاهرة إلا إذا كان تقديرها ضرورياً من الشارت.

أريد النتيجة JSON فقط بهذا الشكل:

{
  "asset": "",
  "timeframe": "",
  "current_price": "",
  "direction": "UP",
  "confidence": 0,
  "entry_delay_minutes": 0,
  "entry_price": "",
  "cancellation_price": "",

  "up_score": 0,
  "down_score": 0,

  "scores": {
    "structure": 0,
    "breakout": 0,
    "liquidity": 0,
    "momentum": 0,
    "candle": 0,
    "rsi": 0,
    "summary": 0,
    "oscillators": 0,
    "moving_averages": 0
  },

  "analysis": {
    "structure": "",
    "breakout": "",
    "liquidity": "",
    "momentum": "",
    "candle": "",
    "rsi": "",
    "summary": "",
    "oscillators": "",
    "moving_averages": ""
  },

  "reason": ""
}

لا تضف أي نص خارج JSON.
"""


# =========================================================
# GEMINI ANALYSIS
# =========================================================

async def analyze_chart(image_bytes):

    response = gemini.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg",
            ),
            ANALYSIS_PROMPT,
        ],
    )

    text = response.text.strip()

    if text.startswith("```"):
        text = text.replace("```json", "")
        text = text.replace("```", "")
        text = text.strip()

    return json.loads(text)


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(data):

    direction = data.get("direction", "UP").upper()

    if direction == "UP":
        icon = "🟢"
        cancel_text = "إذا أغلقت شمعة تحت"
    else:
        icon = "🔴"
        cancel_text = "إذا أغلقت شمعة فوق"

    scores = data.get("scores", {})

    total = (
        scores.get("structure", 0)
        + scores.get("breakout", 0)
        + scores.get("liquidity", 0)
        + scores.get("momentum", 0)
        + scores.get("candle", 0)
        + scores.get("rsi", 0)
        + scores.get("summary", 0)
        + scores.get("oscillators", 0)
        + scores.get("moving_averages", 0)
    )

    now = datetime.now(UTC_MINUS_3)

    delay = int(data.get("entry_delay_minutes", 0))

    entry_time = now + timedelta(minutes=delay)

    entry_time_text = entry_time.strftime("%H:%M")

    if delay == 0:
        entry_label = "الآن"
    else:
        entry_label = f"بعد {delay} دقيقة"

    return (
        "🎓 ZinoProSignalAI\n\n"
        f"🎯 Confidence: {data.get('confidence', 0)}%\n"
        f"📊 {data.get('asset', 'Unknown')} · "
        f"⏱ {data.get('timeframe', 'Unknown')}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"{icon} القرار: {direction}\n\n"

        f"🕐 الدخول: {entry_label}\n"
        f"⏰ وقت الدخول: {entry_time_text}\n\n"

        f"💵 سعر الدخول: {data.get('entry_price', 'N/A')}\n"
        f"🛑 إلغاء {cancel_text} "
        f"{data.get('cancellation_price', 'N/A')}\n\n"

        "📊 SIGNAL SCORE\n"
        f"Structure          {scores.get('structure', 0)}/2\n"
        f"Breakout           {scores.get('breakout', 0)}/2\n"
        f"Liquidity          {scores.get('liquidity', 0)}/1\n"
        f"Momentum           {scores.get('momentum', 0)}/2\n"
        f"Candle             {scores.get('candle', 0)}/2\n"
        f"RSI                {scores.get('rsi', 0)}/1\n"
        f"Summary            {scores.get('summary', 0)}/2\n"
        f"Oscillators        {scores.get('oscillators', 0)}/2\n"
        f"Moving Averages    {scores.get('moving_averages', 0)}/2\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"TOTAL              {total}/18\n\n"

        f"📌 Structure: {data.get('analysis', {}).get('structure', '')}\n"
        f"📌 Breakout: {data.get('analysis', {}).get('breakout', '')}\n"
        f"📌 Liquidity: {data.get('analysis', {}).get('liquidity', '')}\n"
        f"📌 Momentum: {data.get('analysis', {}).get('momentum', '')}\n"
        f"📌 Candle: {data.get('analysis', {}).get('candle', '')}\n"
        f"📌 RSI: {data.get('analysis', {}).get('rsi', '')}\n"
        f"📌 Summary: {data.get('analysis', {}).get('summary', '')}\n"
        f"📌 Oscillators: {data.get('analysis', {}).get('oscillators', '')}\n"
        f"📌 Moving Averages: {data.get('analysis', {}).get('moving_averages', '')}\n\n"

        f"📝 السبب:\n{data.get('reason', '')}"
    )


# =========================================================
# PHOTO HANDLER
# =========================================================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_owner(update):
        await update.message.reply_text(
            "⛔ هذا البوت خاص."
        )
        return

    status = await update.message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:

        photo = update.message.photo[-1]

        telegram_file = await context.bot.get_file(photo.file_id)

        image_buffer = io.BytesIO()

        await telegram_file.download_to_memory(image_buffer)

        image_bytes = image_buffer.getvalue()

        data = await analyze_chart(image_bytes)

        signal = format_signal(data)

        await status.edit_text(signal)

    except json.JSONDecodeError:

        await status.edit_text(
            "⚠️ Gemini أعاد نتيجة غير صالحة.\n"
            "أعد إرسال Screenshot."
        )

    except Exception as error:

        logger.exception("Analysis error")

        await status.edit_text(
            "⚠️ حدث خطأ أثناء التحليل.\n\n"
            f"{str(error)[:500]}"
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):

    logger.error(
        "Unhandled error: %s",
        context.error,
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler,
        )
    )

    app.add_error_handler(error_handler)

    logger.info("ZinoProSignalAI started.")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
