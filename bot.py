import os
import io
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from PIL import Image
from google import genai
from google.genai import types


logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

client = genai.Client(api_key=GEMINI_API_KEY)


ANALYSIS_PROMPT = """
You are a cautious technical-analysis assistant.

Analyze ONLY the trading-chart screenshot the user uploaded.

Return exactly this structure in Arabic:

📊 الزوج: ...
⏱️ الإطار الزمني: ...
📈 الاتجاه: صاعد / هابط / عرضي / غير واضح
🎯 الإشارة: CALL / PUT / NO TRADE
⭐ الثقة التقديرية: ...%
🔎 السبب: ...

Rules:
- If the chart, candles, timeframe, indicators, or asset name are not readable, use NO TRADE.
- Do not invent missing prices or indicators.
- CALL means a bullish directional bias.
- PUT means a bearish directional bias.
- NO TRADE means insufficient or conflicting evidence.
- Treat OTC charts as especially uncertain.
- Do not guarantee profit.
- This is analysis only, not financial advice.
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا 👋\n"
        "أرسل لي Screenshot من الرسم البياني وسأحلله.\n\n"
        "سأرجع لك CALL أو PUT أو NO TRADE مع سبب مختصر.\n"
        "لا توجد إشارة مضمونة."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📸 أرسل صورة واضحة للرسم البياني.\n"
        "يفضل أن يظهر اسم الأصل، الإطار الزمني، والشموع والمؤشرات."
    )


async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔎 جاري تحليل الصورة...")

    try:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        data = await tg_file.download_as_bytearray()

        image = Image.open(io.BytesIO(data)).convert("RGB")

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        image_bytes = buffer.getvalue()

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg"
                ),
                ANALYSIS_PROMPT
            ],
        )

        result = response.text.strip()
        await msg.edit_text(result)

    except Exception:
        logging.exception("Analysis failed")
        await msg.edit_text(
            "❌ تعذر تحليل الصورة الآن.\n"
            "تأكد من إعداد GEMINI_API_KEY وأن الصورة واضحة."
        )


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ZinoQuotexSignalAI is running")

    def log_message(self, format, *args):
        return


def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logging.info(f"Web server running on port {port}")
    server.serve_forever()


def main():
    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )
    web_thread.start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, photo))

    logging.info("Telegram bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
