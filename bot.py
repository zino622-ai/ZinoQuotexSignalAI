import os 
import asyncio
import io
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
You are a cautious and highly accurate technical-analysis assistant specialized in reading trading-chart screenshots.

Analyze ONLY the trading-chart screenshot uploaded by the user.

Your task is to carefully analyze visible candlesticks, price structure, momentum, support and resistance, breakouts, rejections, and any visible technical indicators.

Return EXACTLY this structure in Arabic:

📊 الزوج: ...
⏱️ الإطار الزمني: ...
📈 الاتجاه: صاعد / هابط / غير واضح
🎯 الإشارة: CALL / PUT / NO TRADE
🕐 وقت الدخول: ...
⏱️ مدة الصفقة: 1M / 2M / 5M / 15M / لا توجد
⭐ الثقة: ...%
🔎 السبب: ...

RULES:

1. DIRECTION
- The final direction MUST be only one of:
  - صاعد
  - هابط
  - غير واضح
- NEVER use "عرضي" as the final direction.
- NEVER use "جانبي" or "ranging" as the final direction.
- Determine the dominant direction from the visible price structure.

2. CANDLESTICK ANALYSIS
- Analyze the last 5-15 visible candles whenever clearly readable.
- Examine candle bodies, candle size, upper wicks, lower wicks, consecutive bullish candles, consecutive bearish candles, and rejection candles.
- Give more importance to a sequence of candles than to one isolated candle.
- A single opposite candle must NOT automatically reverse the detected direction.
- Strong consecutive bullish candles increase bullish momentum.
- Strong consecutive bearish candles increase bearish momentum.
- Long upper wicks near resistance may indicate rejection.
- Long lower wicks near support may indicate buying rejection.
- Large candle bodies indicate stronger momentum than very small bodies.
- Do not invent candles or patterns that are not visible.

3. MARKET STRUCTURE
- Higher highs and higher lows → صاعد.
- Lower highs and lower lows → هابط.
- Give more weight to recent confirmed price action while considering the preceding trend.
- Do not change a clearly bullish direction to bearish because of one bearish candle.
- Do not change a clearly bearish direction to bullish because of one bullish candle.

4. SUPPORT AND RESISTANCE
- Identify visible support and resistance zones when possible.
- Look for repeated price reactions.
- Consider breakouts only when visibly confirmed.
- Consider rejection only when supported by visible candle behavior.
- Do not invent exact price levels.

5. MOMENTUM
- Evaluate bullish or bearish momentum.
- Strong directional movement with consistent candle structure increases confidence.
- Sudden large candles followed by immediate reversals reduce confidence.
- Strong wicks and conflicting candles reduce confidence.

6. INDICATORS
- Use indicators only if clearly visible.
- Never invent indicators or indicator values.
- Ignore unclear indicators.

7. SIGNAL
- CALL = bullish evidence favors a possible upward move.
- PUT = bearish evidence favors a possible downward move.
- NO TRADE = evidence is insufficient, conflicting, unreadable, or too risky.
- Direction and signal are separate decisions.
- Do not generate CALL or PUT only because the last candle is green or red.
- Require stronger confirmation before CALL or PUT.

8. OTC
- OTC charts are especially uncertain.
- Do not automatically classify OTC as unclear.
- If structure is clear, identify the dominant direction.
- Require stronger confirmation before CALL or PUT on OTC.
- Reduce confidence when there are abnormal movements or sudden reversals.

9. TIMEFRAME
- Read the timeframe only if clearly visible.
- Never invent it.
- If unreadable:
  غير محدد بدقة على الشارت

10. ASSET / PAIR
- Read the pair only if clearly visible.
- Never invent it.
- If unreadable:
  غير محدد

11. CONFIDENCE
- Use a realistic value from 0% to 100%.
- Never claim certainty or guaranteed profit.
- Conflicting evidence must reduce confidence.

12. TRADE DURATION
Choose the duration based on the visible chart structure and strength.

Possible durations:
- 1M
- 2M
- 5M
- 15M

Do NOT always choose 2M.

General guidance:
- Very short-term strong confirmation → 1M or 2M.
- Clear directional movement with reasonable momentum → 5M.
- Stronger and more established movement on a suitable timeframe → 15M.
- Weak or conflicting setup → NO TRADE and no duration.

13. ENTRY TIME
The Python program will calculate a future entry time after Gemini finishes the analysis.

DO NOT invent or calculate an entry time from the screenshot.

14. IMPORTANT
If the setup is not strong enough:
🎯 الإشارة: NO TRADE
🕐 وقت الدخول: لا يوجد
⏱️ مدة الصفقة: لا توجد

Never guarantee profit.
Never fabricate information.
This is technical analysis only, not financial advice.

The analysis must be based ONLY on what is visibly present in the uploaded screenshot.
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا 👋\n"
        "أرسل Screenshot من الرسم البياني وسأحلله.\n\n"
        "سأرجع لك الاتجاه والإشارة ومدة الصفقة ووقت دخول مستقبلي مناسب للتحليل.\n"
        "لا توجد إشارة مضمونة."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📸 أرسل صورة واضحة للرسم البياني.\n"
        "يفضل أن يظهر اسم الأصل والإطار الزمني والشموع والمؤشرات."
    )

async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text(
        "⚡ جاري تحليل الصورة بسرعة..."
    )

    try:
        telegram_photo = update.message.photo[-1]

        tg_file = await telegram_photo.get_file()
        data = await tg_file.download_as_bytearray()

        image = Image.open(
            io.BytesIO(data)
        ).convert("RGB")

        image.thumbnail((1400, 1400))

        buffer = io.BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=80,
            optimize=True
        )

        image_bytes = buffer.getvalue()

        await msg.edit_text(
            "🧠 جاري تحليل الشارت..."
        )

        def analyze_chart():
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

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(analyze_chart),
                timeout=45
            )

        except asyncio.TimeoutError:
            await msg.edit_text(
                "⏳ التحليل استغرق وقتًا أطول من المتوقع.\n"
                "📸 أرسل الصورة مرة أخرى."
            )
            return

        result = response.text.strip()

        if not result:
            await msg.edit_text(
                "❌ لم يتم الحصول على تحليل واضح.\n"
                "📸 أرسل صورة أوضح للشارت."
            )
            return

        now = datetime.now(
            ZoneInfo("Africa/Algiers")
        )

        duration = None

        if "⏱️ مدة الصفقة: 15M" in result:
            duration = 15
        elif "⏱️ مدة الصفقة: 5M" in result:
            duration = 5
        elif "⏱️ مدة الصفقة: 2M" in result:
            duration = 2
        elif "⏱️ مدة الصفقة: 1M" in result:
            duration = 1

        if duration and (
            "🎯 الإشارة: CALL" in result
            or "🎯 الإشارة: PUT" in result
        ):

            entry_time = now + timedelta(minutes=2)

            entry_time = entry_time.replace(
                second=0,
                microsecond=0
            )

            entry_text = entry_time.strftime("%H:%M")

            lines = result.splitlines()

            cleaned_lines = [
                line
                for line in lines
                if not line.startswith(
                    "🕐 وقت الدخول:"
                )
            ]

            result = "\n".join(cleaned_lines)

            result += (
                f"\n🕐 وقت الدخول: {entry_text}"
                f"\n⏱️ مدة الصفقة: {duration}M"
            )

        else:

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

            result = "\n".join(cleaned_lines)

            result += (
                "\n🕐 وقت الدخول: لا يوجد"
                "\n⏱️ مدة الصفقة: لا توجد"
            )

        await msg.edit_text(result)

    except Exception:
        logging.exception("Analysis failed")

        try:
            await msg.edit_text(
                "❌ حدث خطأ أثناء تحليل الصورة.\n"
                "📸 حاول إرسال الشارت مرة أخرى."
            )
        except Exception:
            pass


class HealthHandler(BaseHTTPRequestHandler):
 

        image_bytes = buffer.getvalue()

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg"
                ),
                ANALYSIS_PROMPT,
            ],
        )

        result = response.text.strip()

        if not result:
            result = (
                "❌ لم يتم الحصول على تحليل واضح.\n"
                "أرسل صورة أوضح للشارت."
            )

        # وقت الجزائر
        now = datetime.now(ZoneInfo("Africa/Algiers"))

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

 async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

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
        telegram_photo = update.message.photo[-1]

        tg_file = await telegram_photo.get_file()
        data = await tg_file.download_as_bytearray()

        image = Image.open(
            io.BytesIO(data)
        ).convert("RGB")

        # نحافظ على جودة الصورة الحالية
        image.thumbnail((1400, 1400))

        buffer = io.BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=80,
            optimize=True
        )

        image_bytes = buffer.getvalue()

        # تحديث الرسالة قبل بدء Gemini
        await msg.edit_text(
            "🧠 جاري تحليل الشارت..."
        )

        # تشغيل Gemini خارج event loop
        def analyze_chart():
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

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(analyze_chart),
                timeout=45
            )

        except asyncio.TimeoutError:

            await msg.edit_text(
                "⏳ التحليل استغرق وقتًا أطول من المتوقع.\n"
                "📸 أرسل الصورة مرة أخرى."
            )
            return

        result = response.text.strip()

        if not result:
            await msg.edit_text(
                "❌ لم يتم الحصول على تحليل واضح.\n"
                "📸 أرسل صورة أوضح للشارت."
            )
            return

        # 🇩🇿 وقت الجزائر
        now = datetime.now(
            ZoneInfo("Africa/Algiers")
        )

        # استخراج مدة الصفقة
        duration = None

        if "⏱️ مدة الصفقة: 15M" in result:
            duration = 15

        elif "⏱️ مدة الصفقة: 5M" in result:
            duration = 5

        elif "⏱️ مدة الصفقة: 2M" in result:
            duration = 2

        elif "⏱️ مدة الصفقة: 1M" in result:
            duration = 1

        # إذا كانت CALL أو PUT
        if duration and (
            "🎯 الإشارة: CALL" in result
            or "🎯 الإشارة: PUT" in result
        ):

            # وقت دخول مستقبلي
            entry_time = now + timedelta(minutes=2)

            entry_time = entry_time.replace(
                second=0,
                microsecond=0
            )

            entry_text = entry_time.strftime("%H:%M")

            lines = result.splitlines()

            cleaned_lines = [
                line
                for line in lines
                if not line.startswith(
                    "🕐 وقت الدخول:"
                )
            ]

            result = "\n".join(cleaned_lines)

            result += (
                f"\n🕐 وقت الدخول: {entry_text}"
 

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
