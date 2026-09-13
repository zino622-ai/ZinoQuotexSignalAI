import os
import io
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
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
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != OWNER_ID:

        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )

        return

    await update.message.reply_text(
        "👋 مرحبًا بك في ZinoQuotexSignalAI\n\n"
        "📸 أرسل صورة واضحة لشارت Quotex.\n"
        "وسأحلل الاتجاه وأعطيك إشارة CALL أو PUT."
    )


# =========================================================
# /HELP
# =========================================================

async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != OWNER_ID:

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
        "• أكبر قدر ممكن من حركة السعر"
    )


# =========================================================
# PHOTO ANALYSIS
# =========================================================

async def photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != OWNER_ID:

        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )

        return

    await update.message.reply_text(
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

        await update.message.reply_text(
            result
        )

    except Exception as e:

        print(
            f"BOT ERROR: {repr(e)}"
        )

        await update.message.reply_text(
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
 main()ers"))

        # استخراج مدة الصفقة التي اختارها Gemini
        duration = None

        if "⏱️ مدة الصفقة: 15M" in result:
            duration = 15
        elif "⏱️ مدة الصفقة: 5M" in result:
            duration = 5
        elif "⏱️ مدة الصفقة: 2M" in result:
            duration = 2
        elif "⏱️ مدة الصفقة: 1M" in result:
            duration = 1

        # إذا كانت الإشارة CALL أو PUT ووجدت مدة
        if duration and (
            "🎯 الإشارة: CALL" in result
            or "🎯 الإشارة: PUT" in result
        ):
            # نعطي وقت تجهيز قبل الدخول
            entry_time = now + timedelta(minutes=2)

            # تقريب إلى بداية الدقيقة
            entry_time = entry_time.replace(
                second=0,
                microsecond=0
            )

            entry_text = entry_time.strftime("%H:%M")

            # إذا كان Gemini قد وضع وقتًا سابقًا،
            # نحذفه ونضع الوقت المستقبلي الذي حسبه البرنامج.
            lines = result.splitlines()

            cleaned_lines = [
                line for line in lines
                if not line.startswith("🕐 وقت الدخول:")
            ]

            result = "\n".join(cleaned_lines)

            result += (
                f"\n🕐 وقت الدخول: {entry_text}"
                f"\n⏱️ مدة الصفقة: {duration}M"
            )

        else:
            # لا نعطي وقت دخول لإشارة غير صالحة
            lines = result.splitlines()

            cleaned_lines = [
                line for line in lines
                if not line.startswith("🕐 وقت الدخول:")
                and not line.startswith("⏱️ مدة الصفقة:")
            ]

            result = "\n".join(cleaned_lines)

            result += (
                "\n🕐 وقت الدخول: لا يوجد"
                "\n⏱️ مدة الصفقة: لا توجد"
            )

        await msg.edit_text(result)

    except Exception:
        logging.exception("Analysis failed")

        await msg.edit_text(
            "❌ تعذر تحليل الصورة الآن.\n"
            "تأكد من أن الصورة واضحة وأن إعدادات Gemini صحيحة."
        )


class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b"ZinoQuotexSignalAI is running"
        )

    def log_message(self, format, *args):
        return


def start_web_server():
    port = int(
        os.environ.get("PORT", 10000)
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    logging.info(
        f"Web server running on port {port}"
    )

    server.serve_forever()


def main():

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    app = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_cmd)
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo
        )
    )

    logging.info(
        "Telegram bot starting..."
    )

    app.run_polling()


if __name__ == "__main__":
    main()
