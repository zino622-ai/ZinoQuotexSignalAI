import os
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from PIL import Image
from google import genai
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================
# CONFIG
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
OWNER_ID = int(os.environ["OWNER_ID"])
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

client = genai.Client(api_key=GEMINI_API_KEY)


# =========================
# WIN / LOSS COUNTERS
# =========================

WIN_COUNT = 0
LOSS_COUNT = 0


# =========================
# RENDER HEALTH SERVER
# =========================

PORT = int(os.environ.get("PORT", 10000))


class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ZinoQuotexSignalAI is running")

    def log_message(self, format, *args):
        return


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


Thread(target=run_health_server, daemon=True).start()


# =========================
# ANALYSIS PROMPT
# =========================

ANALYSIS_PROMPT = """
أنت محلل محترف لرسوم Quotex OTC على إطار M1.

حلل صورة الشارت المرسلة فقط، ولا تخترع أي معلومات غير ظاهرة.

اعتمد على:

1. Market Structure
2. Liquidity
3. Momentum
4. Price Action
5. Confirmation Candle

حدد الاتجاه الأقوى الحالي بناءً على حركة السعر والبنية والسيولة والزخم.

يجب أن تعطي إشارة واحدة فقط:

🟢 CALL (UP)
أو
🔴 PUT (DOWN)

ممنوع إعطاء:
NO SIGNAL
NEUTRAL
WAIT
HOLD

حتى لو كانت الحركة ضعيفة، اختر الاتجاه الذي تراه أقوى، لكن اجعل نسبة الثقة واقعية.

نسبة الثقة يجب أن تكون بين 55% و85%.

لا تعتبر نسبة الثقة ضماناً للربح.

استخدم هذا التنسيق بالضبط:

🎯 الإشارة: 🟢 CALL (UP) XX%

📊 نسبة الثقة: XX%
📊 الأصل: ...
📊 الإطار الزمني: M1
🧭 الاتجاه: ...
📈 الاتجاه القصير: ...

🔹 Market Structure:
...

🔹 Momentum:
...

🔹 Price Action:
...

🔹 شمعة التأكيد:
...

🔹 السبب:
...

لا تضف Support / Resistance.

لا تعطِ أكثر من إشارة واحدة.
"""


# =========================
# START
# =========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    await update.message.reply_text(
        "🤖 ZinoQuotexSignalAI جاهز.\n\n"
        "📸 أرسل صورة الشارت لتحليلها.\n\n"
        "🟢 /win — تسجيل صفقة رابحة\n"
        "🔴 /loss — تسجيل صفقة خاسرة\n"
        "📊 /stats — عرض الإحصائيات"
    )


# =========================
# HELP
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    await update.message.reply_text(
        "📖 الأوامر:\n\n"
        "/start - تشغيل البوت\n"
        "/help - المساعدة\n"
        "/win - تسجيل WIN\n"
        "/loss - تسجيل LOSS\n"
        "/stats - إحصائيات الصفقات\n\n"
        "📸 أرسل صورة الشارت للحصول على التحليل."
    )


# =========================
# WIN
# =========================

async def win_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global WIN_COUNT

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    WIN_COUNT += 1

    await update.message.reply_text(
        f"🟢 WIN تم تسجيلها بنجاح.\n\n"
        f"🟢 WIN: {WIN_COUNT}\n"
        f"🔴 LOSS: {LOSS_COUNT}"
    )


# =========================
# LOSS
# =========================

async def loss_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global LOSS_COUNT

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    LOSS_COUNT += 1

    await update.message.reply_text(
        f"🔴 LOSS تم تسجيلها بنجاح.\n\n"
        f"🟢 WIN: {WIN_COUNT}\n"
        f"🔴 LOSS: {LOSS_COUNT}"
    )


# =========================
# STATS
# =========================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    total = WIN_COUNT + LOSS_COUNT

    if total > 0:
        win_rate = (WIN_COUNT / total) * 100
    else:
        win_rate = 0

    await update.message.reply_text(
        "📊 إحصائيات الصفقات\n\n"
        f"🟢 WIN: {WIN_COUNT}\n"
        f"🔴 LOSS: {LOSS_COUNT}\n"
        f"📈 المجموع: {total}\n"
        f"🎯 نسبة النجاح: {win_rate:.2f}%"
    )


# =========================
# PHOTO ANALYSIS
# =========================

async def analyze_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    message = await update.message.reply_text(
        "🔍 جاري تحليل الشارت..."
    )

    try:

        photo = update.message.photo[-1]

        file = await context.bot.get_file(photo.file_id)

        image_bytes = await file.download_as_bytearray()

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        image.thumbnail((1400, 1400))

        prompt = ANALYSIS_PROMPT

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, image]
        )

        result = response.text

        await message.edit_text(result)

    except Exception as e:

        await message.edit_text(
            f"❌ حدث خطأ أثناء التحليل:\n\n{e}"
        )


# =========================
# MAIN
# =========================

def main():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))

    # WIN / LOSS / STATS
    app.add_handler(CommandHandler("win", win_command))
    app.add_handler(CommandHandler("loss", loss_command))
    app.add_handler(CommandHandler("stats", stats_command))

    # PHOTO
    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            analyze_photo
        )
    )

    print("🤖 ZinoQuotexSignalAI is running...")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
