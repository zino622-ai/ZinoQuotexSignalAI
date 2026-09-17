import os
import io
from datetime import datetime, timedelta, timezone
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
    filters
)

# ============================================================
# إعدادات البيئة
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
OWNER_ID = os.getenv("OWNER_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

OWNER_ID = int(OWNER_ID)

client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# إعداد الوقت
# Quotex عند المستخدم مضبوط على UTC-3
# والفريم المستخدم 2 دقائق
# ============================================================

QUOTEX_TZ = timezone(timedelta(hours=-3))
CANDLE_MINUTES = 2


def get_next_candle_time():
    """
    يحسب بداية شمعة 2M القادمة حسب توقيت UTC-3.
    """
    now = datetime.now(QUOTEX_TZ)

    # بداية شمعة الـ 2 دقائق الحالية
    minute_block = (now.minute // CANDLE_MINUTES) * CANDLE_MINUTES

    current_candle = now.replace(
        minute=minute_block,
        second=0,
        microsecond=0
    )

    # بداية الشمعة القادمة
    next_candle = current_candle + timedelta(minutes=CANDLE_MINUTES)

    return next_candle


# ============================================================
# Health Server - Render
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ZINOSIGNASLQQ is running")

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


# ============================================================
# البرومبت الرئيسي
# ============================================================

SYSTEM_PROMPT = """
أنت محلل احترافي لتحليل شارتات Quotex من صورة الشاشة.

مهمتك تحليل الصورة وإعطاء اتجاه واحد فقط:
CALL (UP) أو PUT (DOWN).

لا تستخدم NO SIGNAL أبداً.

التحليل يجب أن يعتمد على حركة السعر الظاهرة في الصورة فقط.

============================================================
1. MARKET STRUCTURE
============================================================

حلل:

- Higher Highs (HH)
- Higher Lows (HL)
- Lower Highs (LH)
- Lower Lows (LL)
- Break of Structure (BOS)
- Change of Character (CHoCH)
- استمرار الاتجاه
- احتمال انعكاس الاتجاه

حدد هل السوق:

Bullish
Bearish
Range / Sideways

لا تخترع Structure غير واضح في الصورة.

============================================================
2. LIQUIDITY
============================================================

ابحث عن:

- Previous High
- Previous Low
- Equal Highs
- Equal Lows
- Liquidity Sweep
- Fake Breakout
- Stop Hunt
- Rejection بعد أخذ السيولة

إذا لم تكن السيولة واضحة، لا تخترعها.

============================================================
3. MOMENTUM
============================================================

حلل قوة الحركة الحالية:

- حجم الشموع
- سرعة الحركة
- قوة الإغلاق
- استمرار الزخم
- ضعف الزخم
- تسارع الحركة
- تباطؤ الحركة

حدد هل Momentum:

Strong Bullish
Strong Bearish
Weak Bullish
Weak Bearish
Mixed

============================================================
4. PRICE ACTION
============================================================

ابحث عن:

- Breakout
- Pullback
- Retest
- Rejection
- Engulfing
- Fake Breakout
- Continuation
- Reversal
- Compression
- Expansion

ركز على آخر الشموع القريبة من السعر الحالي.

============================================================
5. CURRENT CANDLE
============================================================

ركز بشكل خاص على الشمعة الحالية وآخر الشموع.

حلل:

- اتجاه الشمعة
- جسم الشمعة
- الظلال
- مكان الإغلاق
- هل يوجد رفض سعري
- هل يوجد ضغط شرائي
- هل يوجد ضغط بيعي

============================================================
6. CONFIRMATION
============================================================

ابحث عن شمعة تأكيد واضحة قبل اتخاذ الاتجاه.

مثلاً:

Bullish rejection
Bearish rejection
Bullish engulfing
Bearish engulfing
Strong continuation candle
Breakout confirmation
Retest confirmation

لا تعتمد على شمعة واحدة فقط إذا كان السياق العام يعاكسها.

============================================================
7. CALL VS PUT
============================================================

قارن الأدلة التي تدعم:

CALL (UP)

مع الأدلة التي تدعم:

PUT (DOWN)

ثم اختر اتجاه واحد فقط.

إذا كانت الأدلة مختلطة، اختر الاتجاه الذي لديه دعم أكبر من:

Structure
Liquidity
Momentum
Price Action
Confirmation

لا تعطي اتجاهين.

============================================================
8. CONFIDENCE
============================================================

أعط نسبة ثقة واقعية من 55% إلى 95%.

لا تجعل النسبة دائماً عالية.

إذا كان التحليل قوي جداً:
80% - 95%

إذا كان جيداً:
70% - 79%

إذا كان متوسطاً:
60% - 69%

إذا كان ضعيفاً:
55% - 59%

مهم:
نسبة الثقة ليست ضماناً للنتيجة وليست احتمال ربح حقيقي.

============================================================
9. TIME
============================================================

Quotex عند المستخدم مضبوط على:

UTC-3:00

التحويل المستخدم:

2M

وقت الدخول يجب أن يكون:

بداية شمعة 2M القادمة.

لا تعطِ وقت الدخول الحالي إذا كانت الشمعة الحالية مازالت مستمرة.

مثال:

إذا كان الوقت الحالي:

01:07:30

فبداية شمعة 2M القادمة تكون:

01:08

إذا كان الوقت:

01:09:40

فبداية الشمعة القادمة:

01:10

استخدم توقيت UTC-3.

============================================================
10. IMPORTANT
============================================================

لا تستخدم:

RSI
MACD
ADX
Moving Average
EMA
SMA
Stochastic
Bollinger Bands
أو أي Indicator

إلا إذا كانت ظاهرة فعلياً في الصورة، ولا تعتمد عليها في القرار.

اعتمد أساساً على:

Price Action
Market Structure
Liquidity
Momentum
Confirmation

لا تضف قسم Support / Resistance مستقل.

لا تعطِ WIN أو LOSS.

لا تقل إن الإشارة مضمونة.

لا تستخدم NO SIGNAL.

============================================================
OUTPUT
============================================================

أخرج النتيجة بهذا الشكل بالضبط:

🎯 الإشارة: 🟢 CALL (UP) XX%

📊 نسبة الثقة: XX%
📊 الأصل: اسم الأصل كما يظهر في الصورة
📊 الإطار الزمني: الفريم الظاهر في الشارت

🧭 الاتجاه: Bullish / Bearish / Range
📈 الاتجاه القصير: Bullish / Bearish

🕐 وقت الدخول: HH:MM

━━━━━━━━━━━━━━━━━━

📌 Market Structure
شرح مختصر جداً.

💧 Liquidity
شرح مختصر جداً.

⚡ Momentum
شرح مختصر جداً.

📈 Price Action
شرح مختصر جداً.

🕯 شمعة التأكيد
شرح مختصر جداً.

📝 السبب
سبب مختصر ومباشر يوضح لماذا تم اختيار CALL أو PUT.

============================================================

مهم جداً:

- اتجاه واحد فقط.
- لا تستخدم NO SIGNAL.
- وقت الدخول فقط.
- وقت الدخول = بداية شمعة 2M القادمة.
- التوقيت UTC-3.
- لا تضف وقت انتهاء.
- لا تخترع معلومات غير ظاهرة في الصورة.
"""


# ============================================================
# /start
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        return

    await update.message.reply_text(
        "🔥 ZINOSIGNASLQQ جاهز\n\n"
        "📸 أرسل صورة الشارت للتحليل."
    )


# ============================================================
# تحليل الصورة
# ============================================================

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

        # ----------------------------------------------------
        # تحميل الصورة
        # ----------------------------------------------------

        photo = update.message.photo[-1]

        telegram_file = await context.bot.get_file(
            photo.file_id
        )

        image_bytes = await telegram_file.download_as_bytearray()

        image = Image.open(
            io.BytesIO(image_bytes)
        ).convert("RGB")

        # تصغير الصورة لتسريع التحليل
        image.thumbnail((1400, 1400))

        # ----------------------------------------------------
        # حساب وقت الدخول
        # ----------------------------------------------------

        next_candle = get_next_candle_time()

        entry_time = next_candle.strftime("%H:%M")

        # ----------------------------------------------------
        # إرسال الطلب إلى Gemini
        # ----------------------------------------------------

        prompt = SYSTEM_PROMPT + f"""

وقت الدخول المحسوب حسب UTC-3:

{entry_time}

استخدم هذا الوقت في النتيجة النهائية.

حلل الصورة الآن وأعطني النتيجة بالصيغة المطلوبة.
"""

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                prompt,
                image
            ]
        )

        result = response.text.strip()

        # ----------------------------------------------------
        # إرسال النتيجة
        # ----------------------------------------------------

        await update.message.reply_text(
            result
        )

    except Exception as e:

        await update.message.reply_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            + str(e)
        )


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application
        .builder()
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
        MessageHandler(
            filters.PHOTO,
            analyze_chart
        )
    )

    print(
        "ZINOSIGNASLQQ started"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
