import os
import io
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from google import genai
from google.genai import types
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
OWNER_ID = int(os.environ["OWNER_ID"])

GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
)

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# SESSION STATISTICS
# =========================================================

wins = 0
losses = 0


def get_stats_text():

    total = wins + losses

    if total > 0:
        win_rate = (wins / total) * 100
        loss_rate = (losses / total) * 100
    else:
        win_rate = 0
        loss_rate = 0

    return (
        "📊 إحصائيات الجلسة\n\n"
        f"📈 إجمالي الصفقات: {total}\n"
        f"🟢 WIN: {wins}\n"
        f"🔴 LOSS: {losses}\n\n"
        f"🎯 نسبة النجاح: {win_rate:.1f}%\n"
        f"📉 نسبة الخسارة: {loss_rate:.1f}%"
    )


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.end_headers()

        self.wfile.write(
            b"ZinoQuotexSignalAI is running."
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"🌐 Health server running on port {port}"
    )

    server.serve_forever()


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(update: Update):

    user = update.effective_user

    return (
        user is not None
        and user.id == OWNER_ID
    )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )

        return

    await update.message.reply_text(
        "👋 مرحبًا بك في ZinoQuotexSignalAI\n\n"
        "📸 أرسل صورة واضحة لشارت Quotex.\n"
        "وسأحلل الاتجاه وأعطيك إشارة CALL أو PUT.\n\n"
        "📊 لمعرفة إحصائيات الصفقات:\n"
        "/stats\n\n"
        "🔄 لتصفير الإحصائيات:\n"
        "/reset"
    )


# =========================================================
# /HELP
# =========================================================

async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص."
        )

        return

    await update.message.reply_text(
        "📸 أرسل صورة واضحة للرسم البياني.\n\n"
        "يفضل أن يظهر:\n"
        "• اسم الأصل\n"
        "• الإطار الزمني\n"
        "• الشموع\n"
        "• أكبر قدر ممكن من حركة السعر\n\n"
        "📊 /stats — إحصائيات الجلسة\n"
        "🔄 /reset — تصفير الإحصائيات"
    )


# =========================================================
# /STATS
# =========================================================

async def stats_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص."
        )

        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🔄 تصفير الإحصائيات",
                callback_data="reset_stats"
            )
        ]
    ]

    await update.message.reply_text(
        get_stats_text(),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# /RESET
# =========================================================

async def reset_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global wins, losses

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص."
        )

        return

    wins = 0
    losses = 0

    await update.message.reply_text(
        "🔄 تم تصفير إحصائيات الجلسة.\n\n"
        "📈 إجمالي الصفقات: 0\n"
        "🟢 WIN: 0\n"
        "🔴 LOSS: 0"
    )


# =========================================================
# WIN / LOSS BUTTONS
# =========================================================

async def result_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global wins, losses

    query = update.callback_query

    await query.answer()

    if query.from_user.id != OWNER_ID:

        await query.answer(
            "🔒 غير مسموح.",
            show_alert=True
        )

        return

    data = query.data

    if data == "win":

        wins += 1
        result_text = "🟢 تم تسجيل الصفقة: WIN"

    elif data == "loss":

        losses += 1
        result_text = "🔴 تم تسجيل الصفقة: LOSS"

    elif data == "reset_stats":

        wins = 0
        losses = 0

        await query.edit_message_text(
            "🔄 تم تصفير إحصائيات الجلسة.\n\n"
            "📈 إجمالي الصفقات: 0\n"
            "🟢 WIN: 0\n"
            "🔴 LOSS: 0"
        )

        return

    else:
        return

    # إزالة أزرار WIN / LOSS بعد تسجيل النتيجة
    await query.edit_message_reply_markup(
        reply_markup=None
    )

    await query.message.reply_text(
        f"{result_text}\n\n"
        f"{get_stats_text()}"
    )


# =========================================================
# PHOTO ANALYSIS
# =========================================================

async def photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )

        return

    status_message = await update.message.reply_text(
        "🔍 جاري تحليل الشارت..."
    )

    try:

        photo_file = await update.message.photo[-1].get_file()

        image_bytes = await photo_file.download_as_bytearray()

        image = Image.open(
            io.BytesIO(image_bytes)
        )

        image.thumbnail(
            (1400, 1400)
        )

        buffer = io.BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=90
        )

        image_data = buffer.getvalue()

        prompt = """
أنت محلل Price Action محترف متخصص في تحليل شارتات
Quotex قصيرة المدى.

حلل الصورة المرفقة فقط، ولا تخترع معلومات غير ظاهرة في الشارت.

ركز على:

1. Market Structure
2. Liquidity
3. Liquidity Sweep
4. Momentum
5. Price Action
6. آخر الشموع
7. شمعة التأكيد
8. الاتجاه العام
9. الاتجاه القصير
10. مناطق الانعكاس المحتملة

أريد منك إعطاء اتجاه واحد فقط:

CALL (UP)
أو
PUT (DOWN)

لا تستخدم NO SIGNAL.
حتى إذا كانت الإشارة ضعيفة، اختر الاتجاه الأكثر احتمالًا
واذكر أن الثقة منخفضة بدل إعطاء NO SIGNAL.

لا تعتمد على RSI أو MACD أو أي مؤشر غير ظاهر في الصورة.

ركز على:
Structure + Liquidity + Momentum + Confirmation Candle.

الإجابة يجب أن تكون بهذا الشكل بالضبط:

🎯 الإشارة: 🟢 CALL (UP) XX%

📊 نسبة الثقة: XX%
📊 الأصل: اسم الأصل الظاهر في الشارت
📊 الإطار الزمني: الإطار الظاهر
🧭 الاتجاه: Bullish / Bearish
📈 الاتجاه القصير: Bullish / Bearish

📐 Market Structure
شرح مختصر.

⚡ Momentum
شرح مختصر.

💧 Liquidity
شرح مختصر.

📊 Price Action
شرح مختصر.

🕯️ شمعة التأكيد
اذكر نوع الشمعة ولماذا تعتبر تأكيدًا.

📈 السبب
اذكر السبب الرئيسي وراء اختيار CALL أو PUT.

مهم:
- لا تكتب NO SIGNAL.
- لا تعطِ نسبة ثقة 100%.
- لا تخترع سعرًا أو أصلًا أو إطارًا زمنيًا غير ظاهر.
- إذا كانت الصورة غير واضحة، قل إن القراءة محدودة لكن اختر الاتجاه الأكثر احتمالًا.
- لا تقدم ضمانًا للربح.
"""

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(
                    data=image_data,
                    mime_type="image/jpeg"
                ),
                prompt
            ]
        )

        result = response.text

        keyboard = [
            [
                InlineKeyboardButton(
                    "🟢 WIN",
                    callback_data="win"
                ),
                InlineKeyboardButton(
                    "🔴 LOSS",
                    callback_data="loss"
                )
            ]
        ]

        await status_message.edit_text(
            result,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    except Exception as e:

        print(
            f"BOT ERROR: {repr(e)}"
        )

        await status_message.edit_text(
            "❌ حدث خطأ أثناء تحليل الصورة.\n"
            "حاول إرسال الشارت مرة أخرى."
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "🚀 Starting ZinoQuotexSignalAI..."
    )

    health_thread = Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

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
        CommandHandler(
            "help",
            help_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "reset",
            reset_cmd
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            result_callback,
            pattern="^(win|loss|reset_stats)$"
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo
        )
    )

    print(
        "🤖 Telegram bot starting..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
