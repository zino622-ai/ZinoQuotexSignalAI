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


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
OWNER_ID = int(os.environ["OWNER_ID"])

GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

client = genai.Client(api_key=GEMINI_API_KEY)


# =========================================================
# HEALTH SERVER FOR RENDER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ZinoQuotexSignalAI is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


Thread(target=start_health_server, daemon=True).start()


# =========================================================
# ANALYSIS PROMPT
# =========================================================

ANALYSIS_PROMPT = """
أنت محلل فني متخصص في تحليل شارتات Quotex OTC على إطار M1 (1 دقيقة).

مهمتك تحليل صورة الشارت المرسلة فقط، ثم إعطاء إشارة تداول واحدة واضحة:

CALL (UP)
أو
PUT (DOWN)

استخدم منهجية:

Market Structure + Liquidity + Momentum + Price Action + Confirmation Candle

لا تعتمد على مؤشر واحد فقط، ولا تخترع بيانات أو مستويات سعرية غير ظاهرة بوضوح في الشارت.

========================
قواعد التحليل
========================

1. Market Structure

حدد الهيكل العام للسوق:

Higher Highs + Higher Lows = Bullish

Lower Highs + Lower Lows = Bearish

إذا كان الهيكل مختلطًا، حدد الاتجاه الأقوى والأحدث.

2. Momentum

حلل قوة الحركة الأخيرة:

- قوة الشموع
- سرعة الحركة
- تسلسل الشموع
- هل الزخم مستمر؟
- هل الزخم بدأ يضعف؟
- هل توجد علامات Exhaustion؟

3. Liquidity

ابحث عن:

- Liquidity Sweep
- سحب سيولة القمم
- سحب سيولة القيعان
- Fake Breakout
- Stop Hunt

إذا لم يكن Liquidity Sweep واضحًا، لا تدّعي حدوثه.

4. Price Action

حلل:

- Rejection
- Breakout
- Pullback
- Engulfing
- Pin Bar / Hammer
- Shooting Star
- Marubozu
- Continuation
- Reversal

ركز خصوصًا على آخر الشموع.

5. Confirmation Candle

حدد آخر شمعة أو مجموعة شموع تؤكد الإشارة.

اشرح لماذا تعتبر تأكيدًا.

6. القرار النهائي

اختر CALL أو PUT بناءً على مجموع الأدلة.

يمكن أن يكون الاتجاه العام Bearish ولكن الإشارة CALL إذا ظهرت أدلة قوية على ارتداد صاعد قصير المدى، مثل:

Liquidity Sweep للقيعان
+
Rejection قوي
+
Bullish Pinbar / Hammer
+
تحسن Momentum

وبالعكس:

يمكن أن يكون الاتجاه العام Bullish ولكن الإشارة PUT إذا ظهرت أدلة قوية على انعكاس هابط قصير المدى.

لا تجعل الاتجاه العام وحده يحدد الإشارة.

========================
قاعدة مهمة جدًا
========================

يجب أن تكون الإشارة متوافقة مع أقوى دليل موجود في آخر جزء من الشارت.

إذا كان الاتجاه العام هابطًا ولكن توجد إشارة ارتداد صاعد واضحة في آخر الشموع، يمكن إعطاء CALL.

إذا كان الاتجاه العام صاعدًا ولكن توجد إشارة انعكاس هابط واضحة في آخر الشموع، يمكن إعطاء PUT.

لا تعطِ CALL و PUT معًا.

ممنوع استخدام:

NO SIGNAL
NEUTRAL
WAIT
HOLD

يجب دائمًا اختيار:

CALL (UP)

أو

PUT (DOWN)

لكن لا تعطِ ثقة عالية بدون أدلة كافية.

========================
الثقة
========================

أعطِ نسبة ثقة من 55% إلى 85%.

الثقة ليست ضمانًا للربح.

ارفع الثقة عندما تتفق عدة عوامل:

Structure
+
Liquidity
+
Momentum
+
Price Action
+
Confirmation

اخفض الثقة عندما تكون الأدلة متناقضة.

لا تجعل نسبة الثقة عشوائية.

========================
استخراج البيانات
========================

حاول قراءة:

- اسم الأصل
- OTC
- الإطار الزمني

إذا لم تكن البيانات واضحة، اكتب:

غير واضح

ولا تخترع اسم الأصل أو الإطار الزمني.

لا تخترع مستويات سعرية.

لا تذكر رقم سعر إلا إذا كان ظاهرًا بوضوح في الشارت.

========================
صيغة الإجابة
========================

ابدأ مباشرة بهذا الشكل:

🎯 الإشارة: [🟢 CALL (UP) أو 🔴 PUT (DOWN)] [النسبة]%

📊 نسبة الثقة: [النسبة]%
📊 الأصل: [اسم الأصل]
📊 الإطار الزمني: [1 دقيقة (1M) أو ما يظهر في الشارت]
🧭 الاتجاه: [Bullish / Bearish]
📈 الاتجاه القصير: [Bullish / Bearish + وصف مختصر إذا لزم]

📐 Market Structure
اشرح هيكل السوق باختصار، مع ذكر Higher Highs/Higher Lows أو Lower Highs/Lower Lows إذا كانت واضحة.

⚡ Momentum
اشرح قوة الزخم الحالي وهل هو مستمر أو ضعيف أو يظهر عليه Exhaustion.

💧 Liquidity
اشرح وجود Liquidity Sweep أو Stop Hunt فقط إذا كان ظاهرًا بوضوح.

اذكر هل تم سحب Buy-side أو Sell-side Liquidity.

📊 Price Action
اشرح أهم حركة سعرية في آخر الشموع، مثل Rejection أو Breakout أو Pullback أو Engulfing.

🕯️ شمعة التأكيد
حدد نوع شمعة التأكيد إذا كان واضحًا، واشرح لماذا تدعم CALL أو PUT.

📈 السبب
اكتب خلاصة قوية ومباشرة تجمع أهم الأدلة التي أدت إلى الإشارة.

========================
الأسلوب
========================

اكتب بالعربية الواضحة مع المصطلحات الإنجليزية بين قوسين.

كن مختصرًا لكن تحليلك يجب أن يكون مفيدًا ومحددًا.

لا تكرر نفس الفكرة في أكثر من قسم.

لا تذكر Support/Resistance كقسم مستقل.

لا تستخدم RSI أو MACD أو Moving Average إلا إذا كانت ظاهرة فعلًا في الصورة.

لا تفترض وجود مؤشرات غير ظاهرة.

لا تخترع معلومات غير موجودة في الشارت.

وفي النهاية أضف:

---
*تنويه: التداول ينطوي على مخاطر عالية، وهذه القراءة مبنية على التحليل الفني للشارت المرفق فقط ولا تعتبر ضمانًا للربح.*
"""


# =========================================================
# START COMMAND
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    await update.message.reply_text(
        "🤖 ZinoQuotexSignalAI جاهز.\n\n"
        "📸 أرسل صورة الشارت وسأحللها."
    )


# =========================================================
# HELP COMMAND
# =========================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    await update.message.reply_text(
        "📸 أرسل Screenshot للشارت.\n\n"
        "سأعطيك:\n"
        "🎯 CALL أو PUT\n"
        "📊 نسبة الثقة\n"
        "🧭 الاتجاه العام\n"
        "📈 الاتجاه القصير\n"
        "📐 Market Structure\n"
        "⚡ Momentum\n"
        "💧 Liquidity\n"
        "📊 Price Action\n"
        "🕯️ Confirmation Candle\n"
        "📈 السبب"
    )


# =========================================================
# PHOTO ANALYSIS
# =========================================================

async def analyze_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    try:
        await update.message.reply_text(
            "🔍 جاري تحليل الشارت..."
        )

        photo = update.message.photo[-1]

        file = await context.bot.get_file(photo.file_id)

        image_bytes = await file.download_as_bytearray()

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        # تصغير الصورة إذا كانت كبيرة
        image.thumbnail((1400, 1400))

        prompt = ANALYSIS_PROMPT

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                prompt,
                image
            ]
        )

        result = response.text

        if not result:
            result = "⚠️ لم يتم الحصول على تحليل."

        await update.message.reply_text(
            result
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ حدث خطأ أثناء التحليل:\n\n{str(e)}"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

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
