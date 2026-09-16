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
    filters
)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

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


# ============================================================
# GEMINI CLIENT
# ============================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b"ZINOSIGNASLQQ is running"
        )

    def log_message(self, format, *args):
        pass


def run_health_server():

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

    server.serve_forever()


Thread(
    target=run_health_server,
    daemon=True
).start()


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
أنت ZINOSIGNASLQQ، محلل فني متخصص في تحليل الشارتات قصيرة المدى.

حلل صورة الشارت اعتمادًا فقط على حركة السعر والشموع والمعلومات المرئية في الصورة.

ممنوع اختراع أي معلومات غير ظاهرة في الصورة.

ممنوع الاعتماد على أي مؤشر فني موجود أو غير موجود.

التحليل يجب أن يعتمد فقط على:

1. Market Structure
2. Liquidity
3. Momentum
4. Price Action
5. Confirmation Candle


━━━━━━━━━━━━━━━━━━
Market Structure
━━━━━━━━━━━━━━━━━━

حدد الاتجاه الحالي من حركة السعر.

Higher Highs + Higher Lows
= اتجاه صاعد

Lower Highs + Lower Lows
= اتجاه هابط

راقب:

- Break of Structure (BOS)
- Change of Character (CHoCH)
- استمرار الاتجاه
- ضعف الاتجاه
- بداية الانعكاس

لا تعتبر شمعة واحدة تغييرًا كاملًا للهيكل.

يجب النظر إلى تسلسل القمم والقيعان والحركة السابقة.


━━━━━━━━━━━━━━━━━━
Liquidity
━━━━━━━━━━━━━━━━━━

ابحث عن السيولة الواضحة من حركة السعر.

راقب:

- القمم السابقة
- القيعان السابقة
- Equal Highs
- Equal Lows
- Liquidity Sweep
- Fake Breakout

إذا اخترق السعر قمة أو قاعًا ثم عاد بسرعة إلى داخل المنطقة،
اعتبر ذلك احتمال Liquidity Sweep.

لا تفترض وجود Liquidity Sweep إذا لم يكن واضحًا من الصورة.

لا تخترع مناطق سيولة غير موجودة.


━━━━━━━━━━━━━━━━━━
Momentum
━━━━━━━━━━━━━━━━━━

قيّم قوة الحركة من السعر والشموع فقط.

راقب:

- سرعة حركة السعر
- حجم الشموع مقارنة بالشموع السابقة
- استمرار الحركة في نفس الاتجاه
- قوة الإغلاق
- ضعف الزخم
- التباطؤ
- ضغط المشترين
- ضغط البائعين

الشموع الكبيرة المتتابعة مع إغلاقات قوية تدعم استمرار الزخم.

الشموع الصغيرة والمتداخلة تشير إلى ضعف الزخم وعدم وضوح الحركة.

راقب أيضًا إذا كان السعر يحاول الصعود أو الهبوط ولكنه يفشل في الاستمرار.


━━━━━━━━━━━━━━━━━━
Price Action
━━━━━━━━━━━━━━━━━━

حلل حركة السعر وسياقها.

راقب:

- Breakout
- Pullback
- Retest
- Rejection
- Engulfing
- Fake Breakout
- Continuation
- Reversal

لا تعتمد على شكل شمعة واحدة فقط.

ركز على مكان حدوث الحركة وما الذي حدث قبلها وبعدها.

الاختراق يكون أقوى عندما يتبعه استمرار أو Retest ناجح.

الرفض يكون أقوى عندما يحدث بالقرب من قمة أو قاع واضح.


━━━━━━━━━━━━━━━━━━
Confirmation Candle
━━━━━━━━━━━━━━━━━━

لا تعتمد على التوقع فقط.

ابحث عن شمعة تأكيد واضحة تدعم الاتجاه النهائي.

CALL / UP:

يفضل وجود:

- ضغط شرائي واضح
- إغلاق صاعد قوي
- رفض للهبوط
- Breakout ناجح
- Retest ناجح
- استمرار واضح للاتجاه الصاعد

PUT / DOWN:

يفضل وجود:

- ضغط بيعي واضح
- إغلاق هابط قوي
- رفض للصعود
- Breakout ناجح
- Retest ناجح
- استمرار واضح للاتجاه الهابط


━━━━━━━━━━━━━━━━━━
القرار النهائي
━━━━━━━━━━━━━━━━━━

اجمع الأدلة من:

Market Structure
+
Liquidity
+
Momentum
+
Price Action
+
Confirmation Candle

لا تعتمد على عامل واحد فقط.

قارن الأدلة المؤيدة لـ CALL مع الأدلة المؤيدة لـ PUT.

اختر الاتجاه الذي تدعمه الأدلة الأقوى من حركة السعر الحالية.

إذا كانت الأدلة متعارضة:

اختر الاتجاه الذي لديه دعم أكبر من حركة السعر والشموع،
وقم بخفض نسبة الثقة.

ممنوع استخدام:

NO SIGNAL

يجب إعطاء إشارة واحدة فقط:

CALL (UP)

أو

PUT (DOWN)

حتى عندما تكون الصورة غير مثالية،
يجب اختيار الاتجاه الأكثر دعمًا بالأدلة وخفض نسبة الثقة بدل إعطاء NO SIGNAL.

نسبة الثقة تقديرية وليست ضمانًا للنتيجة.

لا تسجل WIN أو LOSS من نفسك.


━━━━━━━━━━━━━━━━━━
تحليل الشمعة الحالية
━━━━━━━━━━━━━━━━━━

انتبه إلى آخر شمعة ظاهرة في الصورة.

حدد:

- اتجاهها
- قوة إغلاقها
- هل يوجد رفض سعري؟
- هل تؤكد الحركة؟
- هل تعكس الحركة؟
- هل هي شمعة استمرار؟
- هل هي شمعة تردد؟

لا تجعل الشمعة الحالية وحدها سبب القرار.

يجب ربطها بالهيكل والسيولة والزخم وحركة السعر السابقة.


━━━━━━━━━━━━━━━━━━
تجنب الإشارات الضعيفة
━━━━━━━━━━━━━━━━━━

إذا كان السعر يتحرك بشكل جانبي:

لا تعتبر أي حركة صغيرة اتجاهًا قويًا.

إذا كانت الشموع متداخلة:

اعتبر الزخم ضعيفًا.

إذا ظهر Breakout بدون استمرار:

اعتبر احتمال Fake Breakout.

إذا ظهر Liquidity Sweep واضح ثم تأكيد سعري:

أعطِ وزنًا أكبر لهذا السيناريو.

إذا كان الاتجاه واضحًا والزخم مستمرًا:

أعطِ وزنًا أكبر لاستمرار الاتجاه.


━━━━━━━━━━━━━━━━━━
معلومات الصورة
━━━━━━━━━━━━━━━━━━

إذا كان الأصل واضحًا في الصورة، اكتبه.

إذا لم يكن واضحًا:

غير واضح

إذا كان الإطار الزمني واضحًا، اكتبه.

إذا لم يكن واضحًا:

غير واضح

لا تخترع الأصل.

لا تخترع الإطار الزمني.

لا تخترع السعر.

لا تخترع وقت الدخول.

لا تخترع معلومات غير ظاهرة.


━━━━━━━━━━━━━━━━━━
صيغة الإجابة
━━━━━━━━━━━━━━━━━━

استخدم هذا الشكل بالضبط:

🎯 الإشارة: 🟢 CALL (UP) XX%

📊 نسبة الثقة: XX%
📊 الأصل: ...
📊 الإطار الزمني: ...
🧭 الاتجاه: ...
📈 الاتجاه القصير: ...

━━━━━━━━━━━━━━━━━━
Market Structure
━━━━━━━━━━━━━━━━━━

اشرح باختصار ماذا يفعل الهيكل السعري حاليًا.

━━━━━━━━━━━━━━━━━━
Liquidity
━━━━━━━━━━━━━━━━━━

اشرح باختصار أين توجد السيولة وما إذا كان هناك Sweep أو Fake Breakout واضح.

━━━━━━━━━━━━━━━━━━
Momentum
━━━━━━━━━━━━━━━━━━

اشرح قوة الحركة والزخم من خلال الشموع فقط.

━━━━━━━━━━━━━━━━━━
Price Action
━━━━━━━━━━━━━━━━━━

اشرح أهم حركة سعرية تدعم الإشارة.

━━━━━━━━━━━━━━━━━━
شمعة التأكيد
━━━━━━━━━━━━━━━━━━

اذكر الشمعة أو الحركة التي تؤكد الاتجاه النهائي.

━━━━━━━━━━━━━━━━━━
السبب
━━━━━━━━━━━━━━━━━━

• السبب الأول
• السبب الثاني
• السبب الثالث


━━━━━━━━━━━━━━━━━━
ممنوعات
━━━━━━━━━━━━━━━━━━

لا تضف Support/Resistance كقسم مستقل.

لا تضف ADX.

لا تضف DI.

لا تضف Moving Average.

لا تضف Parabolic SAR.

لا تضف RSI.

لا تضف MACD.

لا تضف أي مؤشر فني.

لا تخترع مؤشرات غير ظاهرة.

لا تستخدم معلومات من صور سابقة.

لا تسجل WIN أو LOSS.

لا تعطِ NO SIGNAL.

لا تعطِ أكثر من اتجاه واحد.

لا تعطِ CALL و PUT معًا.

حلل الصورة الحالية فقط.

يجب أن يكون القرار النهائي واضحًا ومباشرًا.
"""


# ============================================================
# START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != OWNER_ID:
        return

    await update.message.reply_text(
        "🔥 ZINOSIGNASLQQ جاهز\n\n"
        "📸 أرسل صورة الشارت للتحليل."
    )


# ============================================================
# ANALYZE CHART
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

        photo = update.message.photo[-1]

        telegram_file = await context.bot.get_file(
            photo.file_id
        )

        image_bytes = await telegram_file.download_as_bytearray()

        image = Image.open(
            io.BytesIO(image_bytes)
        ).convert("RGB")

        image.thumbnail(
            (1600, 1600)
        )

        prompt = SYSTEM_PROMPT + """

حلل هذه الصورة الآن.

أعطني إشارة واحدة فقط:

CALL (UP)

أو

PUT (DOWN)

لا تستخدم NO SIGNAL.

اتبع صيغة الإجابة المحددة في التعليمات.
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


# ============================================================
# MAIN
# ============================================================

def main():

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


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
