import os
import io
import json
import asyncio
import threading
from datetime import timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# =========================
# ENV
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_TEXT = os.getenv("OWNER_ID")

# النموذج الأساسي السريع
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

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


# =========================
# GEMINI CLIENT
# =========================

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(
        timeout=18000
    )
)


# =========================
# TIMEZONE UTC-3
# =========================

UTC_MINUS_3 = timezone(
    timedelta(hours=-3)
)


# =========================
# STATS
# =========================

wins = 0
losses = 0


# =========================
# PROMPT
# =========================

PROMPT = """
أنت محلل فني لشارت Quotex.

حلل الصورة المرسلة فقط.
ممنوع اختراع أي سعر أو وقت أو مؤشر غير ظاهر.

الاستراتيجية الوحيدة:

1. Keltner Channel 20/10
- اتجاه القناة وميلها.
- مكان السعر داخل القناة.
- الاختراقات الواضحة.
- لمس الحد وحده ليس إشارة انعكاس.

2. ADX 14/14
- قوة الاتجاه.
- +DI و -DI إذا كانت ظاهرة.
- ADX وحده لا يحدد الاتجاه.

3. Price Action
- فتح وإغلاق الشموع.
- القمم والقيعان.
- Higher High / Higher Low.
- Lower High / Lower Low.
- جسم الشمعة والظلال.
- الاختراق والإغلاق فوق أو تحت المستوى.
- شمعة التأكيد.

ممنوع استخدام:
RSI
MACD
Moving Average
EMA
SMA
Parabolic SAR
Stochastic
Bollinger Bands
أي مؤشر آخر.

قرار UP:
هيكل صاعد + حركة سعر صاعدة + Keltner داعم + ADX/+DI داعم عندما تكون البيانات ظاهرة.

قرار DOWN:
هيكل هابط + حركة سعر هابطة + Keltner داعم + ADX/-DI داعم عندما تكون البيانات ظاهرة.

لا تجعل كل الإشارات UP.
لا تجعل كل الإشارات DOWN.

الاختراق لا يعتبر مؤكداً بالظل فقط.
الإغلاق هو المهم.

وقت الدخول:
المستخدم يعمل UTC-3.
الدخول يكون في بداية الشمعة القادمة.
استخدم وقت الشارت الظاهر في الصورة.
لا تعط وقت انتهاء.
لا تقل "بعد ساعة" أو "بعد عدة دقائق".
أعط وقت الدخول فقط.

إذا كان وقت الشارت غير واضح:
ضع "غير واضح" بدل اختراع وقت.

شرط الإلغاء:
استخدم مستوى واضح من الصورة.
لا تخترع مستوى.

الثقة:
اجعل النسبة حسب قوة توافق الأدلة الظاهرة.
لا ترفع الثقة بشكل عشوائي.

أرجع JSON فقط.
بدون Markdown.
بدون ```json.
"""


# =========================
# JSON SCHEMA
# =========================

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


# =========================
# HEALTH SERVER
# =========================

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


# =========================
# OWNER CHECK
# =========================

def is_owner(update: Update):

    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


# =========================
# GEMINI REQUEST
# =========================

def gemini_request(
    image_bytes,
    model_name
):

    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type="image/jpeg"
    )

    response = client.models.generate_content(
        model=model_name,
        contents=[
            PROMPT,
            image_part
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SCHEMA,
            temperature=0.1,
            max_output_tokens=700
        )
    )

    return response.text


# =========================
# ANALYZE IMAGE
# =========================

async def analyze_image(image_bytes):

    models_to_try = [
        GEMINI_MODEL
    ]

    # احتياط إذا كان الموديل الأساسي مزدحماً
    if GEMINI_MODEL != "gemini-2.5-flash-lite":
        models_to_try.append(
            "gemini-2.5-flash-lite"
        )

    last_error = None

    for model_name in models_to_try:

        for attempt in range(2):

            try:

                raw = await asyncio.wait_for(
                    asyncio.to_thread(
                        gemini_request,
                        image_bytes,
                        model_name
                    ),
                    timeout=22
                )

                if not raw:
                    raise ValueError(
                        "Gemini returned an empty response"
                    )

                raw = raw.strip()

                if raw.startswith("```"):
                    raw = raw.replace(
                        "```json",
                        ""
                    )
                    raw = raw.replace(
                        "```",
                        ""
                    )
                    raw = raw.strip()

                try:
                    return json.loads(raw)

                except json.JSONDecodeError:
                    raise ValueError(
                        "Gemini returned invalid JSON"
                    )

            except asyncio.TimeoutError:

                last_error = (
                    f"⏱ انتهت مهلة التحليل "
                    f"بعد 22 ثانية "
                    f"({model_name})"
                )

            except Exception as e:

                last_error = str(e)

                error_lower = last_error.lower()

                is_busy = (
                    "503" in error_lower
                    or
                    "unavailable" in error_lower
                    or
                    "high demand" in error_lower
                    or
                    "429" in error_lower
                )

                if is_busy and attempt == 0:
                    await asyncio.sleep(1)
                    continue

                break

    raise ValueError(
        last_error or
        "Gemini analysis failed"
    )


# =========================
# FORMAT SIGNAL
# =========================

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


# =========================
# START
# =========================

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


# =========================
# STATS
# =========================

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


# =========================
# RESET
# =========================

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


# =========================
# PHOTO
# =========================

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
        "🔎 جاري تحليل الشارت..."
    )

    try:

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
            "❌ فشل التحليل بسرعة بدل الانتظار الطويل.\n\n"
            f"{error_text}"
        )


# =========================
# BUTTONS
# =========================

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


# =========================
# ERROR
# =========================

async def error_handler(
    update,
    context
):

    print(
        "Telegram error:",
        context.error
    )


# =========================
# MAIN
# =========================

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

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
