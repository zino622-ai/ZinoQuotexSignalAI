import os
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from PIL import Image
from google import genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters


BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
OWNER_ID = os.getenv("OWNER_ID")


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")


OWNER_ID = int(OWNER_ID)

client = genai.Client(api_key=GEMINI_API_KEY)


class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ZinoQuotexSignalAI is running")

    def log_message(self, format, *args):
        pass


def run_health_server():

    port = int(os.environ.get("PORT", 10000))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    server.serve_forever()


Thread(
    target=run_health_server,
    daemon=True
).start()


SYSTEM_PROMPT = """
أنت ZinoQuotexSignalAI.

حلل صورة الشارت اعتمادًا فقط على المعلومات الظاهرة فيها.

المؤشرات الأساسية:

Parabolic SAR

Moving Average:
MA 5 = أخضر
MA 8 = أصفر
MA 13 = أحمر

ADX / DI:
DI Length = 9
ADX Smoothing = 7

خطوط ADX:
DI+ = أخضر
DI- = برتقالي
ADX = أحمر

حلل العلاقة بين جميع المؤشرات ولا تعتمد على مؤشر واحد فقط.

إذا كانت خطوط ADX/DI الثلاثة مفتوحة ومتباعدة وتتحرك في اتجاه واضح،
اعتبر ذلك دليلًا على قوة الحركة.

إذا بدأت خطوط ADX/DI بالتقاطع والتداخل،
اعتبر ذلك تحذيرًا لاحتمال تغير الاتجاه أو ارتداد أو انعكاس.

لكن لا تعتبر التقاطع وحده تأكيدًا.

راقب أيضًا:

MA5 / MA8 / MA13
Parabolic SAR
Market Structure
Momentum
Price Action
Confirmation Candle

الاتجاه الصاعد:

MA5 > MA8 > MA13
DI+ أقوى من DI-
SAR أسفل السعر
Market Structure صاعد
Momentum صاعد

الاتجاه الهابط:

MA5 < MA8 < MA13
DI- أقوى من DI+
SAR أعلى السعر
Market Structure هابط
Momentum هابط

في حالة الانعكاس، ابحث عن توافق:

ADX/DI crossover
+
MA crossover
+
SAR change
+
Market Structure change
+
Confirmation Candle

لا تعتمد على تقاطع واحد فقط.

ممنوع اختراع معلومات غير ظاهرة في الصورة.

إذا لم يظهر الأصل اكتب:
غير واضح

إذا لم يظهر الإطار الزمني اكتب:
غير واضح

ممنوع استخدام NO SIGNAL.

اختر CALL أو PUT حسب الاتجاه الذي تدعمه الأدلة الأقوى.

إذا كانت الأدلة ضعيفة، اختر الاتجاه الأقوى وخفض نسبة الثقة.

نسبة الثقة تقديرية وليست ضمانًا.

استخدم هذا الشكل:

🎯 الإشارة: 🟢 CALL (UP) XX%

📊 نسبة الثقة: XX%
📊 الأصل: ...
📊 الإطار الزمني: ...
🧭 الاتجاه: ...
📈 الاتجاه القصير: ...

━━━━━━━━━━━━━━━━━━
Market Structure
━━━━━━━━━━━━━━━━━━

...

━━━━━━━━━━━━━━━━━━
ADX / DI
━━━━━━━━━━━━━━━━━━

...

━━━━━━━━━━━━━━━━━━
Moving Averages
━━━━━━━━━━━━━━━━━━

...

━━━━━━━━━━━━━━━━━━
Parabolic SAR
━━━━━━━━━━━━━━━━━━

...

━━━━━━━━━━━━━━━━━━
Momentum
━━━━━━━━━━━━━━━━━━

...

━━━━━━━━━━━━━━━━━━
Price Action
━━━━━━━━━━━━━━━━━━

...

━━━━━━━━━━━━━━━━━━
شمعة التأكيد
━━━━━━━━━━━━━━━━━━

...

━━━━━━━━━━━━━━━━━━
السبب
━━━━━━━━━━━━━━━━━━

• ...
• ...
• ...

لا تسجل WIN أو LOSS من نفسك.
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        return

    await update.message.reply_text(
        "🔥 ZinoQuotexSignalAI جاهز\n\n"
        "📸 أرسل صورة الشارت للتحليل."
    )


async def analyze_chart(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != OWNER_ID:
        return

    try:

        await update.message.reply_text(
            "🔍 جاري تحليل الشارت..."
        )

        photo = update.message.photo[-1]

        telegram_file = await context.bot.get_file(
            photo.file_id
        )

        image_bytes = await telegram_file.download_as_bytearray()

        image = Image.open(
            io.BytesIO(image_bytes)
        ).convert("RGB")

        image.thumbnail((1600, 1600))

        prompt = SYSTEM_PROMPT + """

حلل هذه الصورة الآن.

أعطني إشارة واحدة فقط:
CALL أو PUT.

ثم أعطني التحليل بالشكل المحدد.
"""

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                prompt,
                image
            ]
        )

        result = response.text.strip()

        await update.message.reply_text(
            result
        )

    except Exception as e:

        await update.message.reply_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            + str(e)
        )


def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            analyze_chart
        )
    )

    print("ZinoQuotexSignalAI started")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
