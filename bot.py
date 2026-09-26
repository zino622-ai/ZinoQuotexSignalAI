import os
import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

OWNER_ID = int(OWNER_ID)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("ZinoProSignalAI")


# =========================================================
# GEMINI
# =========================================================

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# TIMEZONE UTC-3
# =========================================================

UTC_MINUS_3 = timezone(
    timedelta(hours=-3)
)


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path == "/health":
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8"
            )
            self.end_headers()

            self.wfile.write(
                b"ZinoProSignalAI is running"
            )

            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()

        self.wfile.write(
            b"ZinoProSignalAI"
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    logger.info(
        "Health server running on port %s",
        PORT
    )

    server.serve_forever()


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(update: Update):

    if not update.effective_user:
        return False

    return (
        update.effective_user.id == OWNER_ID
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "⛔ هذا البوت خاص."
        )

        return

    await update.message.reply_text(
        "🎓 ZinoProSignalAI\n\n"
        "📸 أرسل Screenshot للشارت.\n\n"
        "⚡ سيتم تحليل الشارت "
        "وإعطاؤك الإشارة."
    )


# =========================================================
# ANALYSIS PROMPT
# =========================================================

ANALYSIS_PROMPT = """
أنت محرك التحليل الفني الرئيسي لبوت
ZinoProSignalAI.

حلل Screenshot الخاصة بشارت Quotex.

لا تعتمد على التخمين فقط.
اقرأ الصورة بصرياً وركز على آخر حركة سعرية
والشموع الأخيرة والبنية السعرية.

استخرج قدر الإمكان:

- اسم الزوج
- الفريم
- السعر الحالي
- اتجاه السوق
- الشموع الأخيرة
- القمم والقيعان
- الاختراقات
- السيولة
- الزخم
- المؤشرات الظاهرة

========================================
SIGNAL SCORE
========================================

نظام النقاط الكلي = 18 نقطة.

Structure       = 2
Breakout        = 2
Liquidity       = 1
Momentum        = 2
Candle          = 2
RSI             = 1
Summary         = 2
Oscillators     = 2
Moving Averages = 2

المجموع = 18/18.

========================================
IMPORTANT
========================================

احسب UP و DOWN بشكل منفصل.

لا تجعل UP هو الاتجاه الافتراضي.

لا تجعل DOWN هو الاتجاه الافتراضي.

إذا كانت الأدلة هابطة:
اجعل DOWN يحصل على النقاط المناسبة.

إذا كانت الأدلة صاعدة:
اجعل UP يحصل على النقاط المناسبة.

إذا كان عامل معين غير ظاهر في الصورة،
لا تخترع قراءة دقيقة له.
استخدم فقط ما يمكن استنتاجه من الصورة.

========================================
STRUCTURE
========================================

راقب:

Higher High
Higher Low
Lower High
Lower Low

وكذلك:

Break of Structure
Change of Character
Support break
Resistance break

========================================
BREAKOUT
========================================

ميز بين:

True Breakout
Fake Breakout
Breakout + Confirmation

========================================
LIQUIDITY
========================================

راقب:

Liquidity sweep
High sweep
Low sweep
Rejection after sweep

========================================
MOMENTUM
========================================

استخدم الزخم الظاهر في الشارت
وADX إذا كان موجوداً.

========================================
CANDLE
========================================

راقب:

Bullish Engulfing
Bearish Engulfing
Hammer
Pin Bar
Strong close
Weak close
Long wick
Candle continuation

========================================
RSI
========================================

إذا كان RSI ظاهر:

Overbought
Oversold
Above 50
Below 50
Divergence إذا كان واضحاً

========================================
SUMMARY
========================================

Summary يجب أن يمثل الصورة العامة
للتحليل وليس مؤشراً جديداً.

========================================
OSCILLATORS
========================================

حلل المؤشرات المتذبذبة الظاهرة،
مثل RSI أو MACD أو Stochastic.

لا تخترع مؤشرات غير موجودة.

========================================
MOVING AVERAGES
========================================

إذا كانت Moving Averages ظاهرة:
حلل:

السعر فوق/تحت المتوسطات
اتجاه المتوسطات
تقاطع المتوسطات
ترتيب المتوسطات

لا تخترع قيمة رقمية غير ظاهرة.

========================================
ENTRY
========================================

حدد Entry Delay:

0 دقيقة
أو
1 دقيقة
أو
2 دقيقة

اختَر حسب وضع السوق.

إذا كان هناك انتظار لتأكيد شمعة،
يمكن اختيار 1 أو 2 دقيقة.

========================================
ENTRY PRICE
========================================

حدد سعر الدخول من السعر الظاهر
أو من منطقة الدخول المنطقية الظاهرة.

========================================
CANCELLATION
========================================

للـ UP:

الإلغاء يكون إذا أغلقت شمعة
تحت مستوى الإلغاء.

للـ DOWN:

الإلغاء يكون إذا أغلقت شمعة
فوق مستوى الإلغاء.

يجب أن يكون المستوى منطقياً بالنسبة
للبنية السعرية الأخيرة.

========================================
CONFIDENCE
========================================

Confidence تعبر عن قوة توافق العوامل
الموجودة في الصورة.

لا تضع 90% أو 95% لمجرد إعطاء رقم كبير.

========================================
OUTPUT
========================================

أرجع JSON فقط.

لا تضع Markdown.

لا تضع ```json.

لا تضع أي نص قبل أو بعد JSON.

الشكل المطلوب:

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
"""


# =========================================================
# SAFE JSON CLEANER
# =========================================================

def clean_json(text):

    text = text.strip()

    if text.startswith("```json"):
        text = text[7:]

    elif text.startswith("```"):
        text = text[3:]

    if text.endswith("```"):
        text = text[:-3]

    return text.strip()


# =========================================================
# GEMINI ANALYSIS
# =========================================================

async def analyze_chart(image_bytes):

    response = gemini.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg"
            ),
            ANALYSIS_PROMPT
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json"
        )
    )

    text = response.text

    if not text:
        raise RuntimeError(
            "Gemini returned an empty response"
        )

    text = clean_json(text)

    return json.loads(text)


# =========================================================
# NUMBER FORMAT
# =========================================================

def clean_price(value):

    if value is None:
        return "N/A"

    value = str(value).strip()

    return value


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(data):

    direction = str(
        data.get("direction", "UP")
    ).upper()

    if direction not in ("UP", "DOWN"):
        direction = "UP"

    if direction == "UP":

        icon = "🟢"

        cancel_text = (
            "إذا أغلقت شمعة تحت"
        )

    else:

        icon = "🔴"

        cancel_text = (
            "إذا أغلقت شمعة فوق"
        )

    scores = data.get(
        "scores",
        {}
    )

    structure = int(
        scores.get("structure", 0)
    )

    breakout = int(
        scores.get("breakout", 0)
    )

    liquidity = int(
        scores.get("liquidity", 0)
    )

    momentum = int(
        scores.get("momentum", 0)
    )

    candle = int(
        scores.get("candle", 0)
    )

    rsi = int(
        scores.get("rsi", 0)
    )

    summary = int(
        scores.get("summary", 0)
    )

    oscillators = int(
        scores.get("oscillators", 0)
    )

    moving_averages = int(
        scores.get("moving_averages", 0)
    )

    total = (
        structure
        + breakout
        + liquidity
        + momentum
        + candle
        + rsi
        + summary
        + oscillators
        + moving_averages
    )

    delay = int(
        data.get(
            "entry_delay_minutes",
            0
        )
    )

    if delay < 0:
        delay = 0

    if delay > 2:
        delay = 2

    now = datetime.now(
        UTC_MINUS_3
    )

    entry_time = (
        now + timedelta(
            minutes=delay
        )
    )

    entry_time_text = (
        entry_time.strftime("%H:%M")
    )

    if delay == 0:

        entry_label = "الآن"

    elif delay == 1:

        entry_label = "بعد 1 دقيقة"

    else:

        entry_label = "بعد 2 دقيقة"

    analysis = data.get(
        "analysis",
        {}
    )

    up_score = int(
        data.get(
            "up_score",
            0
        )
    )

    down_score = int(
        data.get(
            "down_score",
            0
        )
    )

    return (
        "🎓 ZinoProSignalAI\n\n"

        f"🎯 Confidence: "
        f"{data.get('confidence', 0)}%\n"

        f"📊 {data.get('asset', 'Unknown')}"
        f" · ⏱ {data.get('timeframe', 'Unknown')}\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"{icon} القرار: {direction}\n\n"

        f"📊 UP Score: {up_score}/18\n"
        f"📊 DOWN Score: {down_score}/18\n\n"

        f"🕐 الدخول: {entry_label}\n"
        f"⏰ وقت الدخول: {entry_time_text}\n\n"

        f"💵 سعر الدخول: "
        f"{clean_price(data.get('entry_price'))}\n"

        f"🛑 إلغاء {cancel_text} "
        f"{clean_price(data.get('cancellation_price'))}\n\n"

        "📊 SIGNAL SCORE\n"

        f"Structure          {structure}/2\n"
        f"Breakout           {breakout}/2\n"
        f"Liquidity          {liquidity}/1\n"
        f"Momentum           {momentum}/2\n"
        f"Candle             {candle}/2\n"
        f"RSI                {rsi}/1\n"
        f"Summary            {summary}/2\n"
        f"Oscillators        {oscillators}/2\n"
        f"Moving Averages    {moving_averages}/2\n"

        "━━━━━━━━━━━━━━━━━━\n"

        f"TOTAL              {total}/18\n\n"

        "📌 ANALYSIS\n"

        f"Structure: "
        f"{analysis.get('structure', '')}\n"

        f"Breakout: "
        f"{analysis.get('breakout', '')}\n"

        f"Liquidity: "
        f"{analysis.get('liquidity', '')}\n"

        f"Momentum: "
        f"{analysis.get('momentum', '')}\n"

        f"Candle: "
        f"{analysis.get('candle', '')}\n"

        f"RSI: "
        f"{analysis.get('rsi', '')}\n"

        f"Summary: "
        f"{analysis.get('summary', '')}\n"

        f"Oscillators: "
        f"{analysis.get('oscillators', '')}\n"

        f"Moving Averages: "
        f"{analysis.get('moving_averages', '')}\n\n"

        f"📝 السبب:\n"
        f"{data.get('reason', '')}"
    )


# =========================================================
# PHOTO HANDLER
# =========================================================

async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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

        telegram_file = (
            await context.bot.get_file(
                photo.file_id
            )
        )

        image_buffer = io.BytesIO()

        await telegram_file.download_to_memory(
            image_buffer
        )

        image_bytes = (
            image_buffer.getvalue()
        )

        logger.info(
            "Screenshot received: %s bytes",
            len(image_bytes)
        )

        data = await analyze_chart(
            image_bytes
        )

        signal = format_signal(
            data
        )

        await status.edit_text(
            signal
        )

        logger.info(
            "Signal generated: %s",
            data.get("direction")
        )

    except json.JSONDecodeError:

        logger.exception(
            "Invalid JSON from Gemini"
        )

        await status.edit_text(
            "⚠️ Gemini أرسل نتيجة غير صالحة.\n"
            "أعد إرسال Screenshot."
        )

    except Exception as error:

        logger.exception(
            "Analysis error"
        )

        await status.edit_text(
            "⚠️ حدث خطأ أثناء تحليل الشارت.\n\n"
            f"{str(error)[:500]}"
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Unhandled error: %s",
        context.error,
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    logger.info(
        "ZinoProSignalAI starting..."
    )

    logger.info(
        "Gemini model: %s",
        GEMINI_MODEL
    )

    logger.info(
        "Render port: %s",
        PORT
    )

    app = (
        Application.builder()
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
        MessageHandler(
            filters.PHOTO,
            photo_handler
        )
    )

    app.add_error_handler(
        error_handler
    )

    logger.info(
        "Telegram application starting..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
