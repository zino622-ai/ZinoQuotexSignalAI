import os
import io
import asyncio
import logging
import threading

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image

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


# ============================================================
# الإعدادات
# ============================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

OWNER_ID = int(os.environ["OWNER_ID"])

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# إعداد Logging
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# Prompt تحليل الشارت
# ============================================================

ANALYSIS_PROMPT = """
أنت محلل فني متخصص في تحليل شارتات التداول قصيرة المدى.

حلل الصورة المرسلة بدقة.

أعطني النتيجة بهذا الشكل بالضبط:

🎯 الإشارة: CALL🟢
أو
🎯 الإشارة: PUT🔴
أو
🎯 الإشارة: NO SIGNAL

📊 نسبة الثقة: XX%

📈 السبب:
اذكر أهم الأسباب الفنية باختصار.

⏱️ مدة الصفقة: 1M
أو 2M أو 5M أو 15M

قواعد مهمة:

- لا تختر CALL أو PUT إذا كانت الصورة غير واضحة.
- إذا لم تكن الإشارة قوية، استخدم NO SIGNAL.
- لا تخترع بيانات غير موجودة في الصورة.
- اعتمد على حركة السعر والشموع والمؤشرات الظاهرة.
- مدة الصفقة يجب أن تكون واحدة فقط من:
  1M
  2M
  5M
  15M

أجب باللغة العربية.
"""


# ============================================================
# /start
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    owner_id = int(os.environ["OWNER_ID"])

    if update.effective_user.id != owner_id:
        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )
        return

    await update.message.reply_text(
        "👋 مرحبًا بك في ZinoQuotexSignalAI\n\n"
        "📸 أرسل صورة واضحة للشارت.\n"
        "🧠 سأقوم بتحليلها باستخدام Gemini.\n\n"
        "يفضل أن يظهر في الصورة:\n"
        "• اسم الأصل\n"
        "• الإطار الزمني\n"
        "• الشموع\n"
        "• المؤشرات"
    )


# ============================================================
# /help
# ============================================================

async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    owner_id = int(os.environ["OWNER_ID"])

    if update.effective_user.id != owner_id:
        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )
        return

    await update.message.reply_text(
        "📸 أرسل صورة واضحة للرسم البياني.\n"
        "يفضل أن يظهر اسم الأصل والإطار الزمني والشموع والمؤشرات."
    )


# ============================================================
# تحليل الصورة
# ============================================================

async def photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # 🔐 السماح لصاحب البوت فقط
    owner_id = int(os.environ["OWNER_ID"])

    if update.effective_user.id != owner_id:
        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )
        return

    msg = await update.message.reply_text(
        "⚡ جاري تحليل الصورة بسرعة..."
    )

    try:

        # ----------------------------------------------------
        # تحميل صورة Telegram
        # ----------------------------------------------------

        telegram_photo = update.message.photo[-1]

        tg_file = await telegram_photo.get_file()

        data = await tg_file.download_as_bytearray()


        # ----------------------------------------------------
        # فتح الصورة وتحويلها
        # ----------------------------------------------------

        image = Image.open(
            io.BytesIO(data)
        ).convert("RGB")


        # ----------------------------------------------------
        # تقليل الحجم مع الحفاظ على الجودة
        # ----------------------------------------------------

        image.thumbnail(
            (1400, 1400)
        )


        buffer = io.BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=80,
            optimize=True
        )

        image_bytes = buffer.getvalue()


        # ----------------------------------------------------
        # تحديث الرسالة
        # ----------------------------------------------------

        await msg.edit_text(
            "🧠 جاري تحليل الشارت..."
        )


        # ----------------------------------------------------
        # تشغيل Gemini خارج Event Loop
        # ----------------------------------------------------

         def analyze_chart():

    last_error = None

    for attempt in range(3):

        try:

            return client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type="image/jpeg"
                    ),
                    ANALYSIS_PROMPT,
                ],
            )

        except Exception as e:

            last_error = e

            error_text = str(e)

            if "503" in error_text or "UNAVAILABLE" in error_text:

                logging.warning(
                    f"Gemini 503 - محاولة {attempt + 1}/3"
                )

                time.sleep(
                    5 * (attempt + 1)
                )

                continue

            raise

    raise last_error


        try:

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    analyze_chart
                ),
                timeout=45
            )

        except asyncio.TimeoutError:

            await msg.edit_text(
                "⏳ التحليل استغرق وقتًا أطول من المتوقع.\n"
                "📸 أرسل الصورة مرة أخرى."
            )

            return


        # ----------------------------------------------------
        # استخراج النتيجة
        # ----------------------------------------------------

        result = response.text.strip()


        if not result:

            await msg.edit_text(
                "❌ لم يتم الحصول على تحليل واضح.\n"
                "📸 أرسل صورة أوضح للشارت."
            )

            return


        # ----------------------------------------------------
        # وقت الجزائر
        # ----------------------------------------------------

        now = datetime.now(
            ZoneInfo("Africa/Algiers")
        )


        # ----------------------------------------------------
        # استخراج مدة الصفقة
        # ----------------------------------------------------

        duration = None

        if "⏱️ مدة الصفقة: 15M" in result:

            duration = 15

        elif "⏱️ مدة الصفقة: 5M" in result:

            duration = 5

        elif "⏱️ مدة الصفقة: 2M" in result:

            duration = 2

        elif "⏱️ مدة الصفقة: 1M" in result:

            duration = 1


        # ----------------------------------------------------
        # التحقق من CALL / PUT
        # ----------------------------------------------------

        if duration and (
            "🎯 الإشارة: CALL" in result
            or "🎯 الإشارة: PUT" in result
        ):

            # وقت دخول مستقبلي
            entry_time = now + timedelta(
                minutes=2
            )

            # تقريب إلى بداية الدقيقة
            entry_time = entry_time.replace(
                second=0,
                microsecond=0
            )

            entry_text = entry_time.strftime(
                "%H:%M"
            )


            # ------------------------------------------------
            # إزالة وقت الدخول القديم إن وجِد
            # ------------------------------------------------

            lines = result.splitlines()

            cleaned_lines = [
                line
                for line in lines
                if not line.startswith(
                    "🕐 وقت الدخول:"
                )
            ]

            result = "\n".join(
                cleaned_lines
            )


            # ------------------------------------------------
            # إضافة وقت الدخول الجديد
            # ------------------------------------------------

            result += (
                f"\n🕐 وقت الدخول: {entry_text}"
                  ) 


        else:

            # ------------------------------------------------
            # إذا لم توجد إشارة صالحة
            # ------------------------------------------------

            lines = result.splitlines()

            cleaned_lines = [
                line
                for line in lines
                if not line.startswith(
                    "🕐 وقت الدخول:"
                )
                and not line.startswith(
                    "⏱️ مدة الصفقة:"
                )
            ]

            result = "\n".join(
                cleaned_lines
            )

            result += (
                "\n🕐 وقت الدخول: لا يوجد"
                "\n⏱️ مدة الصفقة: لا توجد"
            )


        # ----------------------------------------------------
        # إرسال النتيجة
        # ----------------------------------------------------

        await msg.edit_text(
            result
        )


    # ========================================================
    # معالجة الأخطاء
    # ========================================================

    except Exception:

        logging.exception(
            "Analysis failed"
        )

        try:

            await msg.edit_text(
                "❌ حدث خطأ أثناء تحليل الصورة.\n"
                "📸 حاول إرسال الشارت مرة أخرى."
            )

        except Exception:

            pass


# ============================================================
# Health Check لـ Render
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

        self.end_headers()

        self.wfile.write(
            b"ZinoQuotexSignalAI is running"
        )


    def log_message(
        self,
        format,
        *args
    ):

        return


# ============================================================
# Web Server
# ============================================================

def start_web_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    server = HTTPServer(
        (
            "0.0.0.0",
            port
        ),
        HealthHandler
    )

    logging.info(
        f"Web server running on port {port}"
    )

    server.serve_forever()


# ============================================================
# Main
# ============================================================

def main():

    # --------------------------------------------------------
    # تشغيل Web Server في Thread مستقل
    # --------------------------------------------------------

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()


    # --------------------------------------------------------
    # Telegram Application
    # --------------------------------------------------------

    app = (
        Application
        .builder()
        .token(
            TELEGRAM_TOKEN
        )
        .build()
    )


    # --------------------------------------------------------
    # Commands
    # --------------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_cmd
        )
    )


    # --------------------------------------------------------
    # الصور
    # --------------------------------------------------------

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo
        )
    )


    # --------------------------------------------------------
    # تشغيل البوت
    # --------------------------------------------------------

    logging.info(
        "Telegram bot starting..."
    )

    app.run_polling()


# ============================================================
# تشغيل البرنامج
# ============================================================

if __name__ == "__main__":

    main()
