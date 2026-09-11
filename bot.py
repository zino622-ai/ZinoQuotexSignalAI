import os
import io
import os
import io
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
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
You are a cautious and highly accurate technical-analysis assistant specialized in reading trading-chart screenshots.

Analyze ONLY the trading-chart screenshot uploaded by the user.

Your task is to carefully analyze visible candlesticks, price structure, momentum, support and resistance, breakouts, rejections, and any visible technical indicators.

Return EXACTLY this structure in Arabic:

📊 الزوج: ...
⏱️ الإطار الزمني: ...
📈 الاتجاه: صاعد / هابط / غير واضح
🎯 الإشارة: CALL / PUT / NO TRADE
⭐ الثقة التقديرية: ...%
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
- Analyze the last 5-15 visible candles whenever they are clearly readable.
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
- If higher highs and higher lows are dominant, classify the direction as صاعد.
- If lower highs and lower lows are dominant, classify the direction as هابط.
- If the recent structure clearly follows a bullish move, keep the direction صاعد even if there is temporary consolidation.
- If the recent structure clearly follows a bearish move, keep the direction هابط even if there is temporary consolidation.
- Give more weight to recent confirmed price action while considering the preceding trend.
- Do not change a clearly bullish direction to bearish because of one bearish candle.
- Do not change a clearly bearish direction to bullish because of one bullish candle.

4. SUPPORT AND RESISTANCE
- Identify visible support and resistance zones when possible.
- Look for repeated price reactions around these levels.
- A breakout should only be considered meaningful when the price movement visibly confirms it.
- A rejection from support or resistance should be considered when supported by visible candle behavior.
- Do not invent exact price levels if they cannot be read clearly.

5. MOMENTUM
- Evaluate whether bullish or bearish momentum is increasing, decreasing, or remaining uncertain.
- Strong directional movement with consistent candle structure should receive higher confidence.
- Sudden large candles followed by immediate reversals should reduce confidence.
- Strong wicks and conflicting candles should reduce confidence.
- Do not confuse one strong candle with a confirmed trend.

6. INDICATORS
- If technical indicators are clearly visible in the screenshot, use them only as confirmation.
- Never invent an indicator that is not visible.
- Never invent indicator values.
- If indicators are unclear or unreadable, ignore them.

7. DIRECTION PRIORITY
- Bullish structure → صاعد
- Bearish structure → هابط
- Insufficient reliable visual evidence → غير واضح
- The word "عرضي" must NEVER appear as the final direction.
- Do not use "غير واضح" simply because the chart is OTC.
- Use "غير واضح" only when the available visual evidence is genuinely insufficient or strongly conflicting.

8. SIGNAL
- CALL means the visible evidence favors a bullish directional move.
- PUT means the visible evidence favors a bearish directional move.
- NO TRADE means the evidence is insufficient, conflicting, unreadable, or too risky.
- The direction and trade signal are separate decisions.
- A chart can have a direction while still receiving NO TRADE if the entry evidence is weak.
- Do not generate CALL or PUT only because the last candle is green or red.
- Require stronger confirmation before assigning CALL or PUT.

9. OTC
- OTC charts must be treated as especially uncertain.
- Do not automatically classify an OTC chart as unclear.
- If the visible price structure is clear, identify the dominant direction.
- Because OTC markets can contain irregular movements, require stronger confirmation before assigning CALL or PUT.
- Reduce confidence when the chart shows abnormal volatility, sudden reversals, or conflicting candle behavior.

10. TIMEFRAME
- Read the timeframe only if it is clearly visible in the screenshot.
- Never invent a timeframe.
- If the timeframe is not readable, write:
  غير محدد بدقة على الشارت

11. ASSET / PAIR
- Read the asset or currency pair only if it is clearly visible.
- Never invent the pair name.
- If it cannot be identified reliably, write:
  غير محدد

12. CONFIDENCE
- Confidence must be a realistic estimate from 0% to 100%.
- Do not use extremely high confidence unless the chart provides unusually strong and consistent visual evidence.
- Conflicting candles, weak structure, poor image quality, sudden volatility, or unclear levels must reduce confidence.
- Confidence is NOT a guarantee of the result.

13. IMAGE QUALITY
- If the chart, candles, timeframe, asset name, or important price information is too blurry or cropped to analyze reliably, use:
  🎯 الإشارة: NO TRADE
- Do not guess missing information.
- Do not invent prices, indicators, patterns, candles, or market conditions.

14. FINAL REASON
- The reason must briefly explain the main visible evidence behind the direction and signal.
- Mention important candle behavior, market structure, momentum, support/resistance, or rejection when visible.
- Keep the explanation concise and directly related to the screenshot.
- Do not make unsupported claims.

15. SAFETY
- Never guarantee profit.
- Never claim that a signal is certain or guaranteed.
- Never fabricate information.
- This is technical analysis only, not financial advice.

IMPORTANT:
The analysis must be based ONLY on what is visibly present in the uploaded screenshot.
Do not rely on assumptions, previous screenshots, external information, or invented market data.
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
    msg = await update.message.reply_text("⚡ جاري تحليل الصورة...")

    try:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        data = await tg_file.download_as_bytearray()

        image = Image.open(io.BytesIO(data)).convert("RGB")

        # تصغير الصورة لتسريع التحليل
        image.thumbnail((1600, 1600))

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85, optimize=True)
        image_bytes = buffer.getvalue()

        analysis_instruction = """
IMPORTANT:

Analyze the chart first and determine:
1. Direction: صاعد / هابط / غير واضح
2. Signal: CALL / PUT / NO TRADE
3. Recommended trade duration: 1M / 2M / 5M / 15M

Do NOT provide an entry time based on the screenshot time.

The Python program will calculate the future entry time after the analysis.

Your response MUST include:

⏱️ مدة الصفقة المقترحة: 1M / 2M / 5M / 15M

If there is not enough evidence for a valid trade:
🎯 الإشارة: NO TRADE
⏱️ مدة الصفقة المقترحة: لا توجد

Do not invent market data, candle times, or prices.
"""

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg"
                ),
                ANALYSIS_PROMPT,
                analysis_instruction,
            ],
        )

        result = response.text.strip()

        if not result:
            result = "❌ لم يتم الحصول على تحليل واضح."

        # وقت الجزائر الحالي
        now = datetime.now(ZoneInfo("Africa/Algiers"))

        # استخراج مدة الصفقة من نتيجة Gemini
        duration = None

        if "15M" in result:
            duration = 15
        elif "5M" in result:
            duration = 5
        elif "2M" in result:
            duration = 2
        elif "1M" in result:
            duration = 1

        # إذا كانت هناك إشارة فعلية ومدة واضحة،
        # نحسب وقت دخول مستقبلي
        if duration and ("CALL" in result or "PUT" in result):
            from datetime import timedelta

            # إعطاء وقت تجهيز قبل الدخول
            entry_time = now + timedelta(minutes=1)

            # تقريب وقت الدخول إلى بداية الدقيقة التالية
            entry_time = entry_time.replace(second=0, microsecond=0)

            entry_text = entry_time.strftime("%H:%M")

            result += (
                f"\n\n🕐 وقت الدخول المقترح: {entry_text}"
                f"\n⏱️ مدة الصفقة: {duration}M"
            )

                                await msg.edit_text(result)

    except Exception:
        logging.exception("Analysis failed")
        await msg.edit_text(
            "❌ تعذر تحليل الصورة الآن."
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
