import os
import io
import json
import asyncio
import threading
from datetime import timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
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


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_TEXT = os.getenv("OWNER_ID")

# لا نستخدم أي موديل قديم
GEMINI_MODEL = "gemini-3.5-flash-lite"

# توقيت Quotex
UTC_MINUS_3 = timezone(timedelta(hours=-3))


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID_TEXT:
    raise RuntimeError("OWNER_ID is missing")

try:
    OWNER_ID = int(OWNER_ID_TEXT)
except ValueError:
    raise RuntimeError("OWNER_ID must be a number")


# ============================================================
# GEMINI
# ============================================================

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(
        timeout=15000
    )
)


# ============================================================
# STATS
# ============================================================

wins = 0
losses = 0


# ============================================================
# ANALYSIS PROMPT
# ============================================================

PROMPT = """
أنت محلل فني سريع لشارت Quotex.

مهمتك تحليل الصورة المرسلة فقط وإعطاء اتجاه واحد:
UP أو DOWN.

لا تخترع أي معلومة غير ظاهرة في الصورة.

التحليل يعتمد فقط على:

1. Price Action
- فتح وإغلاق الشموع.
- أجسام الشموع.
- الظلال.
- القمم والقيعان.
- Higher High / Higher Low.
- Lower High / Lower Low.
- كسر المستويات.
- إغلاق الشمعة بعد الكسر.
- شمعة التأكيد.

2. Keltner Channel 20/10
- اتجاه القناة.
- ميل القناة.
- مكان السعر داخل القناة.
- الاختراق الواضح.
- لا تعتبر مجرد ملامسة الحد إشارة انعكاس.

3. ADX 14/14
- قوة الاتجاه.
- +DI و -DI إذا كانا ظاهرين.
- ADX وحده لا يحدد الاتجاه.

ممنوع استخدام:
RSI
MACD
EMA
SMA
Moving Average
Parabolic SAR
Stochastic
Bollinger Bands
أي مؤشر آخر.

قواعد القرار:

UP:
إذا كان الهيكل السعري صاعدًا،
والقمم والقيعان تدعم الصعود،
والشموع تدعم الصعود،
والـ Keltner داعم،
وADX/+DI داعم عندما يكون ظاهرًا.

DOWN:
إذا كان الهيكل السعري هابطًا،
والقمم والقيعان تدعم الهبوط،
والشموع تدعم الهبوط،
والـ Keltner داعم،
وADX/-DI داعم عندما يكون ظاهرًا.

لا تجعل كل الإشارات UP.
لا تجعل كل الإشارات DOWN.

الظل وحده لا يعتبر اختراقًا مؤكدًا.
الإغلاق أهم من مجرد اللمس أو الظل.

وقت الدخول:
المستخدم يعمل بتوقيت UTC-3.
الدخول يكون في بداية الشمعة القادمة.
استخدم وقت الشارت الظاهر في الصورة.
أعط وقت الدخول فقط.
لا تعط وقت انتهاء.
لا تقل "بعد ساعة".
لا تقل "بعد عدة دقائق".

إذا كان وقت الشارت غير واضح:
entry_time = "غير واضح"

إذا كان السعر غير واضح:
entry_price = "غير واضح"

شرط الإلغاء:
استخدم مستوى واضحًا ظاهرًا في الصورة.
إذا لم يوجد مستوى واضح:
cancellation = "غير واضح"

الثقة:
ضع نسبة واقعية حسب توافق الأدلة الظاهرة.
لا تعط 90% أو 100% إلا إذا كانت الأدلة قوية جدًا.

الأصل والزمن:
اقرأهما من الصورة.
لا تخترعهما.

أرجع JSON فقط.
بدون Markdown.
بدون ```json.
"""


# ============================================================
# JSON SCHEMA
# ============================================================

SCHEMA = {
    "type": "OBJECT",
    "properties": {

        "decision": {
            "type": "STRING",
            "enum": ["UP", "DOWN"]
        },

        "confidence": {
            "type": "INTEGER"
        },

        "asset": {
            "type": "STRING"
        },

        "timeframe": {
            "type": "STRING"
        },

        "chart_time": {
            "type": "STRING"
        },

        "entry_time": {
            "type": "STRING"
        },

        "entry_price": {
            "type": "STRING"
        },

        "trend": {
            "type": "STRING"
        },

        "short_trend": {
            "type": "STRING"
        },

        "structure": {
            "type": "STRING"
        },

        "keltner": {
            "type": "STRING"
        },

        "adx": {
            "type": "STRING"
        },

        "candle": {
            "type": "STRING"
        },

        "reason": {
            "type": "STRING"
        },

        "cancellation": {
            "type": "STRING"
        }
    },

    "required": [
        "decision",
        "confidence",
        "asset",
        "timeframe",
        "chart_time",
        "entry_time",
        "entry_price",
        "trend",
        "short_trend",
        "structure",
        "keltner",
        "adx",
        "candle",
        "reason",
        "cancellation"
    ]
}


# ============================================================
# HEALTH SERVER
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

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
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
# OWNER
# ============================================================

def is_owner(update: Update):

    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


# ============================================================
# GEMINI REQUEST
# ============================================================

def gemini_request(image_bytes):

    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type="image/jpeg"
    )

    response = client.models.generate_content(

        model=GEMINI_MODEL,

        contents=[
            PROMPT,
            image_part
        ],

        config=types.GenerateContentConfig(

            response_mime_type="application/json",

            response_schema=SCHEMA,

            temperature=0.1,

            max_output_tokens=500
        )
    )

    return response.text


# ============================================================
# ANALYZE
# ============================================================

async def analyze_image(image_bytes):

    try:

        result = await asyncio.wait_for(

            asyncio.to_thread(
                gemini_request,
                image_bytes
            ),

            timeout=14
        )

        if not result:

            raise ValueError(
                "Gemini returned an empty response"
            )

        result = result.strip()

        if result.startswith("```"):

            result = result.replace(
                "```json",
                ""
            )

            result = result.replace(
                "```",
                ""
            )

            result = result.strip()

        try:

            return json.loads(result)

        except json.JSONDecodeError:

            raise ValueError(
                "Gemini returned invalid JSON"
            )

    except asyncio.TimeoutError:

        raise ValueError(
            "Gemini لم يكمل التحليل خلال 14 ثانية."
        )

    except Exception as e:

        error = str(e)

        if "504" in error:

            raise ValueError(
                "Gemini 504: انتهت مهلة التحليل."
            )

        if "503" in error:

            raise ValueError(
                "Gemini 503: الخدمة مشغولة حاليًا."
            )

        if "429" in error:

            raise ValueError(
                "Gemini 429: تم تجاوز حد الطلبات."
            )

        if "404" in error:

            raise ValueError(
                "Gemini 404: تحقق من توفر النموذج."
            )

        raise ValueError(error)


# ============================================================
# FORMAT
# ============================================================

def format_signal(data):

    decision = str(
        data.get(
            "decision",
            "UP"
        )
    ).upper()

    if decision == "DOWN":

        direction = "🔴 DOWN — بيع (Put)"

    else:

        direction = "🟢 UP — شراء (Call)"


    confidence = data.get(
        "confidence",
        0
    )

    asset = data.get(
        "asset",
        "غير واضح"
    )

    timeframe = data.get(
        "timeframe",
        "غير واضح"
    )

    chart_time = data.get(
        "chart_time",
        "غير واضح"
    )

    entry_time = data.get(
        "entry_time",
        "غير واضح"
    )

    entry_price = data.get(
        "entry_price",
        "غير واضح"
    )

    trend = data.get(
        "trend",
        "غير واضح"
    )

    short_trend = data.get(
        "short_trend",
        "غير واضح"
    )

    structure = data.get(
        "structure",
        "غير واضح"
    )

    keltner = data.get(
        "keltner",
        "غير واضح"
    )

    adx = data.get(
        "adx",
        "غير واضح"
    )

    candle = data.get(
        "candle",
        "غير واضح"
    )

    reason = data.get(
        "reason",
        "غير واضح"
    )

    cancellation = data.get(
        "cancellation",
        "غير واضح"
    )


    return (

        "🎓 تحليل زينو\n\n"

        f"✅ Solid Setup · {confidence}%\n"

        f"📊 {asset} · ⏱ {timeframe}\n"

        "━━━━━━━━━━━━━━\n\n"

        f"🎯 القرار: {direction}\n"

        f"🕐 وقت الشارت: {chart_time}\n"

        f"⏰ وقت الدخول: {entry_time}\n"

        f"💵 سعر الدخول: {entry_price}\n"

        "━━━━━━━━━━━━━━\n\n"

        f"📐 الاتجاه: {trend}\n"

        f"📈 الاتجاه القصير: {short_trend}\n"

        f"🏗 البنية السعرية: {structure}\n"

        f"〽️ Keltner 20/10: {keltner}\n"

        f"📊 ADX 14/14: {adx}\n"

        f"🕯 شمعة التأكيد: {candle}\n"

        "━━━━━━━━━━━━━━\n\n"

        f"🧠 شرح زينو: {reason}\n\n"

        f"🛑 شرط إلغاء الإشارة: {cancellation}\n"

        "━━━━━━━━━━━━━━\n\n"

        "⚠️ التحليل مبني على الشارت المرسل."
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    await update.message.reply_text(
        "🤖 ZINOSIGNASLQQ جاهز.\n\n"
        "📸 أرسل صورة الشارت للتحليل."
    )


# ============================================================
# STATS
# ============================================================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    total = wins + losses

    if total:

        winrate = (
            wins / total
        ) * 100

    else:

        winrate = 0


    await update.message.reply_text(

        "📊 إحصائيات التداول\n\n"

        f"✅ WIN: {wins}\n"

        f"❌ LOSS: {losses}\n"

        f"📌 TOTAL: {total}\n"

        f"🎯 WIN RATE: {winrate:.1f}%"
    )


# ============================================================
# RESET
# ============================================================

async def reset_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global wins
    global losses

    if not is_owner(update):
        return

    wins = 0
    losses = 0

    await update.message.reply_text(
        "♻️ تم تصفير الإحصائيات."
    )


# ============================================================
# PHOTO
# ============================================================

async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    if not update.message:
        return

    if not update.message.photo:
        return


    status = await update.message.reply_text(
        "🔎 تحليل سريع..."
    )


    try:

        # أعلى جودة للصورة التي أرسلها Telegram
        photo = update.message.photo[-1]

        telegram_file = await photo.get_file()

        image_buffer = io.BytesIO()

        await telegram_file.download_to_memory(
            out=image_buffer
        )

        image_bytes = image_buffer.getvalue()


        if not image_bytes:

            raise ValueError(
                "الصورة فارغة"
            )


        data = await analyze_image(
            image_bytes
        )


        signal = format_signal(
            data
        )


        keyboard = InlineKeyboardMarkup(

            [
                [

                    InlineKeyboardButton(
                        "✅ WIN",
                        callback_data="trade_win"
                    ),

                    InlineKeyboardButton(
                        "❌ LOSS",
                        callback_data="trade_loss"
                    )

                ]
            ]
        )


        await status.edit_text(

            signal,

            reply_markup=keyboard
        )


    except Exception as e:

        error_text = str(e)

        if len(error_text) > 1200:

            error_text = error_text[:1200]


        await status.edit_text(

            "❌ فشل التحليل.\n\n"
            f"{error_text}"
        )


# ============================================================
# BUTTONS
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global wins
    global losses

    query = update.callback_query

    if not query:
        return


    if query.from_user.id != OWNER_ID:

        await query.answer()

        return


    await query.answer()


    if query.data == "trade_win":

        wins += 1

        await query.message.reply_text(

            "✅ تم تسجيل WIN\n\n"

            f"WIN: {wins}\n"

            f"LOSS: {losses}\n"

            f"TOTAL: {wins + losses}"
        )


    elif query.data == "trade_loss":

        losses += 1

        await query.message.reply_text(

            "❌ تم تسجيل LOSS\n\n"

            f"WIN: {wins}\n"

            f"LOSS: {losses}\n"

            f"TOTAL: {wins + losses}"
        )


# ============================================================
# ERROR
# ============================================================

async def error_handler(
    update,
    context
):

    print(
        "Telegram error:",
        context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    application = (

        ApplicationBuilder()

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

        CommandHandler(
            "stats",
            stats
        )
    )


    application.add_handler(

        CommandHandler(
            "resetstats",
            reset_stats
        )
    )


    application.add_handler(

        MessageHandler(
            filters.PHOTO,
            photo_handler
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


    print(
        "ZinoQuotexSignalAI started."
    )

    print(
        "Gemini model:",
        GEMINI_MODEL
    )


    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
