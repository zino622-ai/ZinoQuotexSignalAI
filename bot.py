import os
import io
import json
import asyncio
import threading
from datetime import datetime, timedelta, timezone
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


# =========================
# ENVIRONMENT
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_TEXT = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

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

client = genai.Client(api_key=GEMINI_API_KEY)


# =========================
# TIMEZONE UTC-3
# =========================

UTC_MINUS_3 = timezone(timedelta(hours=-3))


# =========================
# STATS
# =========================

wins = 0
losses = 0


# =========================
# ANALYSIS PROMPT
# =========================

PROMPT = """
أنت محلل فني متخصص في تحليل صور شارت Quotex.

حلل الصورة المرسلة فقط، ولا تخترع أي بيانات غير ظاهرة.

الاستراتيجية المسموح بها فقط:

1) Keltner Channel 20/10
- راقب اتجاه القناة وميلها.
- راقب مكان السعر داخل القناة.
- راقب الخروج أو الاختراق الواضح من القناة.
- لمس الحد وحده لا يعني انعكاس.

2) ADX 14/14
- استخدم ADX لتقييم قوة الاتجاه.
- استخدم +DI و -DI لتحديد الاتجاه عندما تكون ظاهرة.
- ADX وحده لا يحدد UP أو DOWN.

3) Price Action
- راقب فتح وإغلاق الشموع.
- راقب القمم والقيعان.
- راقب Higher High / Higher Low.
- راقب Lower High / Lower Low.
- راقب قوة جسم الشمعة والظلال.
- راقب الاختراقات.
- لا تعتبر الظل وحده اختراقاً مؤكداً.
- الإغلاق مهم لتأكيد الاختراق.
- راقب شمعة التأكيد الأخيرة.

ممنوع تماماً استخدام أو ذكر:
RSI
MACD
Moving Average
EMA
SMA
Parabolic SAR
Stochastic
Bollinger Bands
أي مؤشر آخر غير Keltner Channel و ADX.

قواعد القرار:

UP:
إذا كان هيكل السعر صاعداً، والزخم صاعداً، وKeltner يدعم الصعود، وADX/+DI يدعمان الاتجاه عندما تكون هذه البيانات ظاهرة.

DOWN:
إذا كان هيكل السعر هابطاً، والزخم هابطاً، وKeltner يدعم الهبوط، وADX/-DI يدعمان الاتجاه عندما تكون هذه البيانات ظاهرة.

لا تجعل جميع الإشارات UP.
لا تجعل جميع الإشارات DOWN.
اختر الاتجاه الذي تدعمه الصورة فعلاً.

إذا كان هناك اختراق، لا تؤكده إلا إذا كان إغلاق الشمعة يدعمه.

وقت الدخول:
- المستخدم يعمل بتوقيت UTC-3.
- الدخول يكون في بداية الشمعة القادمة مباشرة.
- احسب وقت الدخول من وقت الشارت الظاهر في الصورة.
- لا تعطِ وقت انتهاء.
- لا تقل "بعد X دقائق" إذا كان وقت الدخول يمكن تحديده.
- لا تعطِ وقت دخول بعد عدة شموع.
- إذا كانت الشمعة الحالية 2 دقائق، فالدخول يكون عند بداية الشمعة التالية حسب وقت الشارت.
- لا تخترع وقتاً إذا كان وقت الشارت غير واضح.

شرط الإلغاء:
اكتب شرط إلغاء مرتبطاً بمستوى أو بنية سعر ظاهرة فعلاً في الصورة.
لا تخترع مستوى سعري غير ظاهر.
مثال:
"إذا أغلقت الشمعة الحالية تحت مستوى X قبل الدخول."

الثقة:
- اجعل النسبة مبنية على توافق الأدلة الظاهرة.
- لا تعطِ 90% أو 95% بدون توافق قوي جداً.
- لا ترفع الثقة لمجرد وجود مؤشر واحد.
- يجب أن يكون القرار واضحاً UP أو DOWN.

أجب JSON فقط.
لا تضف Markdown.
لا تضف ```json.
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
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"ZinoQuotexSignalAI is running"
        )

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

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

def is_owner(update: Update) -> bool:

    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


# =========================
# GEMINI ANALYSIS
# =========================

async def analyze_image(image_bytes: bytes):

    def run_gemini():

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
                max_output_tokens=900
            )
        )

        return response.text

    raw = await asyncio.to_thread(run_gemini)

    if not raw:
        raise ValueError("Gemini returned an empty response")

    raw = raw.strip()

    try:
        return json.loads(raw)

    except json.JSONDecodeError:

        if raw.startswith("```"):
            raw = raw.replace("```json", "")
            raw = raw.replace("```", "")
            raw = raw.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("Gemini returned invalid JSON")


# =========================
# FORMAT SIGNAL
# =========================

def format_signal(data):

    decision = str(data.get("decision", "UP")).upper()

    if decision == "DOWN":
        direction = "🔴 DOWN — بيع (Put)"
    else:
        direction = "🟢 UP — شراء (Call)"

    confidence = data.get("confidence", 0)

    asset = data.get("asset", "غير واضح")
    timeframe = data.get("timeframe", "غير واضح")

    chart_time = data.get("chart_time", "غير واضح")
    entry_time = data.get("entry_time", "غير واضح")

    entry_price = data.get("entry_price", "غير واضح")

    trend = data.get("trend", "غير واضح")
    short_trend = data.get("short_trend", "غير واضح")

    structure = data.get("structure", "غير واضح")
    keltner = data.get("keltner", "غير واضح")
    adx = data.get("adx", "غير واضح")
    candle = data.get("candle", "غير واضح")

    reason = data.get("reason", "غير واضح")
    cancellation = data.get(
        "cancellation",
        "إذا تغيرت البنية السعرية قبل الدخول."
    )

    text = (
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
        "⚠️ التحليل مبني على الصورة المرسلة فقط."
    )

    return text


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_owner(update):
        return

    await update.message.reply_text(
        "🤖 ZINOSIGNASLQQ جاهز.\n\n"
        "📸 أرسل صورة الشارت وسأحللها."
    )


# =========================
# STATS
# =========================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_owner(update):
        return

    total = wins + losses

    if total > 0:
        winrate = (wins / total) * 100
    else:
        winrate = 0

    await update.message.reply_text(
        "📊 إحصائيات التداول\n\n"
        f"✅ WIN: {wins}\n"
        f"❌ LOSS: {losses}\n"
        f"📌 المجموع: {total}\n"
        f"🎯 Win Rate: {winrate:.1f}%"
    )


# =========================
# RESET STATS
# =========================

async def reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

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
# PHOTO HANDLER
# =========================

async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    message = update.message

    if not message or not message.photo:
        return

    status_message = await message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:

        photo = message.photo[-1]

        file = await photo.get_file()

        image_buffer = io.BytesIO()

        await file.download_to_memory(
            out=image_buffer
        )

        image_bytes = image_buffer.getvalue()

        if not image_bytes:
            raise ValueError("الصورة فارغة")

        data = await analyze_image(image_bytes)

        signal_text = format_signal(data)

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

        await status_message.edit_text(
            signal_text,
            reply_markup=keyboard
        )

    except Exception as e:

        error_text = str(e)

        if len(error_text) > 1000:
            error_text = error_text[:1000]

        await status_message.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{error_text}"
        )


# =========================
# WIN / LOSS BUTTONS
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
            f"✅ تم تسجيل WIN\n\n"
            f"WIN: {wins}\n"
            f"LOSS: {losses}\n"
            f"TOTAL: {wins + losses}"
        )

    elif query.data == "trade_loss":

        losses += 1

        await query.message.reply_text(
            f"❌ تم تسجيل LOSS\n\n"
            f"WIN: {wins}\n"
            f"LOSS: {losses}\n"
            f"TOTAL: {wins + losses}"
        )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
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
        "ZinoQuotexSignalAI started successfully."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
