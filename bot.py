import os
import io
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================
# ZinoQuotexSignalAI
# Quotex Smart Price Action Analysis
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

OWNER_ID = os.getenv("OWNER_ID")

# النموذج الأساسي من Render
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
)

# ترتيب النماذج الاحتياطية
GEMINI_FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

client = genai.Client(
    api_key=GEMINI_API_KEY
)

ALGERIA_TZ = ZoneInfo(
    "Africa/Algiers"
)

# Quotex timezone requested by user: UTC-3
QUOTEX_TZ = ZoneInfo(
    "Etc/GMT+3"
)

stats = {
    "win": 0,
    "loss": 0,
}


# ============================================================
# OWNER
# ============================================================

def is_owner(user_id):

    if not OWNER_ID:
        return True

    try:
        return str(user_id) == str(int(OWNER_ID))

    except Exception:
        return str(user_id) == str(OWNER_ID)


# ============================================================
# TIME
# ============================================================

def get_current_quotex_time():

    return datetime.now(
        ALGERIA_TZ
    ).astimezone(
        QUOTEX_TZ
    )


def calculate_entry_time(minutes_ahead):

    now = get_current_quotex_time()

    # بداية الشمعة القادمة
    next_candle = (
        now + timedelta(minutes=1)
    ).replace(
        second=0,
        microsecond=0
    )

    # إذا اختار Gemini بعد 1 دقيقة:
    # ندخل في بداية الشمعة القادمة.
    #
    # إذا اختار 2:
    # ندخل بعد شمعتين.
    #
    # إذا اختار 3:
    # ندخل بعد ثلاث شموع.

    entry = next_candle + timedelta(
        minutes=minutes_ahead - 1
    )

    return entry.strftime("%H:%M")


# ============================================================
# GEMINI PROMPT
# ============================================================

ANALYSIS_PROMPT = """
أنت محلل فني متخصص في تحليل Screenshot لشارت Quotex.

مهم جدًا:
لا تفترض أن الشارت M1.

أنت بنفسك حدد الإطار الزمني الظاهر في Screenshot إذا كان واضحًا، مثل:
1M
2M
3M
5M
15M
أو أي إطار زمني آخر ظاهر بوضوح.

إذا كان الإطار الزمني غير واضح في الصورة، اكتب:
غير واضح

لا تخترع الإطار الزمني.

حلل Screenshot فقط، ولا تخترع أي معلومة غير ظاهرة.

============================================================
1. الاتجاه الهابط Downtrend
============================================================

إذا كان السوق هابطًا، ابحث عن:

- Lower High
- Lower Low
- قمم أقل
- قيعان أقل

في الاتجاه الهابط تكون الأولوية لصفقات:

PUT / DOWN

عندما تؤكد الشموع استمرار الهبوط.

============================================================
2. الاتجاه الصاعد Uptrend
============================================================

إذا كان السوق صاعدًا، ابحث عن:

- Higher High
- Higher Low
- قمم أعلى
- قيعان أعلى

في الاتجاه الصاعد تكون الأولوية لصفقات:

CALL / UP

عندما تؤكد الشموع استمرار الصعود.

============================================================
3. السوق العرضي Consolidation
============================================================

إذا كان السعر يتحرك أفقيًا بين منطقتين:

- اعتبر السوق Consolidation.
- راقب الحد العلوي والحد السفلي.
- عند الاقتراب من المقاومة ابحث عن PUT بعد تأكيد شموعي.
- عند الاقتراب من الدعم ابحث عن CALL بعد تأكيد شموعي.
- لا تدخل مباشرة بمجرد لمس الدعم أو المقاومة.
- يجب انتظار تأكيد من الشموع.

============================================================
4. الدعم والمقاومة
============================================================

حدد الدعم والمقاومة من القمم والقيعان الظاهرة في الشارت.

عند وصول السعر إلى:

الدعم:
ابحث عن CALL فقط إذا ظهرت إشارة تأكيد شموعي.

المقاومة:
ابحث عن PUT فقط إذا ظهرت إشارة تأكيد شموعي.

ممنوع الدخول مباشرة عند لمس المستوى.

انتظر تأكيد الشموع.

مهم:
استخدم الدعم والمقاومة في التحليل،
لكن لا تضعهما كقسم مستقل في النتيجة النهائية.

============================================================
5. الشموع
============================================================

ركز بقوة على:

- قوة الإغلاق.
- مكان الإغلاق.
- طول الجسم.
- الفتيل العلوي.
- الفتيل السفلي.
- رفض السعر.
- هل الإغلاق يؤكد الحركة أم يعكسها.

Hammer / Pin Bar:

إذا ظهرت شمعة Hammer أو Pin Bar بظل سفلي طويل وجسم صغير، اعتبرها رفضًا للهبوط ويمكن أن تدعم CALL إذا كان السياق السعري يؤكد ذلك.

إذا ظهر رفض صعود واضح بظل علوي طويل، يمكن أن يدعم PUT إذا كان السياق السعري يؤكد ذلك.

============================================================
6. Engulfing
============================================================

راقب الشموع الابتلاعية.

Bullish Engulfing:
شمعة صاعدة تبتلع الشمعة السابقة ويمكن أن تدعم CALL.

Bearish Engulfing:
شمعة هابطة تبتلع الشمعة السابقة ويمكن أن تدعم PUT.

لا تعتمد على Engulfing وحدها.

يجب أن تتوافق مع:
- اتجاه السعر
أو
- الدعم والمقاومة
أو
- بنية السوق.

============================================================
7. RSI
============================================================

استخدم RSI إذا كان ظاهرًا بوضوح في Screenshot.

إذا كان RSI فوق 70:
السوق في تشبع شرائي.

ابحث عن PUT إذا أكدت الشموع ذلك.

إذا كان RSI تحت 30:
السوق في تشبع بيعي.

ابحث عن CALL إذا أكدت الشموع ذلك.

لا تخترع قيمة RSI إذا لم تكن ظاهرة.

============================================================
8. EMA 5 و EMA 13
============================================================

استخدم EMA 5 و EMA 13 إذا كانا ظاهرين بوضوح.

إذا كان EMA 5 فوق EMA 13:
هذا يدعم CALL / UP.

إذا كان EMA 5 تحت EMA 13:
هذا يدعم PUT / DOWN.

لا تعتمد على EMA وحده.

يجب أن يتوافق مع حركة السعر والشموع.

لا تخترع تقاطعًا غير ظاهر.

============================================================
9. اختيار وقت الدخول
============================================================

هذه نقطة مهمة جدًا.

لا تدخل تلقائيًا في الشمعة التالية دائمًا.

بعد تحليل الشارت، حدد كم شمعة يحتاجها السعر حتى تصبح فرصة الدخول أفضل.

اختر وقت الدخول بناءً على:

- قوة الاتجاه.
- قرب السعر من الدعم أو المقاومة.
- هل ظهرت شمعة تأكيد بالفعل.
- هل يحتاج السعر إلى ارتداد.
- هل يحتاج السعر إلى شمعة أو شمعتين إضافيتين للتأكيد.
- قوة Momentum.
- Price Action.
- بنية السوق.

يمكن أن يكون الدخول:

1 دقيقة
2 دقيقة
3 دقائق

أو أكثر إذا كان ذلك ضروريًا ومناسبًا للشارت.

لا تختر وقتًا عشوائيًا.

إذا كانت الإشارة مؤكدة جدًا:
يمكن اختيار دخول قريب.

إذا كانت تحتاج تأكيدًا إضافيًا:
اختر دخولًا أبعد.

أعطني قيمة رقمية واضحة لعدد الدقائق حتى الدخول.

مثال:

وقت الدخول بعد: 2 دقيقة

لا تعطِ وقت انتهاء.

============================================================
10. اختيار CALL أو PUT
============================================================

يجب اختيار اتجاه واحد فقط:

CALL (UP)

أو

PUT (DOWN)

ممنوع:

NO SIGNAL

ممنوع:

NEUTRAL

إذا كانت الأدلة ضعيفة، اختر الاتجاه الذي تدعمه الأدلة الموجودة مع تخفيض نسبة الثقة.

لا تختار الاتجاه عشوائيًا.

============================================================
11. نسبة الثقة
============================================================

أعطِ نسبة ثقة بين:

50% و95%

النسبة تعكس قوة الأدلة الموجودة في Screenshot فقط.

لا تقل إن الصفقة مضمونة.

============================================================
12. ترتيب قوة الأدلة
============================================================

أعطِ الأولوية إلى:

1. الاتجاه وبنية السوق.
2. Higher High / Higher Low.
3. Lower High / Lower Low.
4. الدعم والمقاومة.
5. إغلاق وافتتاح الشموع.
6. Hammer / Pin Bar.
7. Engulfing.
8. RSI إذا كان ظاهرًا.
9. EMA 5 و EMA 13 إذا كانا ظاهرين.

============================================================
13. النتيجة
============================================================

أخرج النتيجة بهذا الشكل:

🎯 الإشارة: 🟢 CALL (UP) XX%

📊 نسبة الثقة: XX%
📊 الأصل: [اسم الأصل]
📊 الإطار الزمني: [الفريم الظاهر]
⏱️ الدخول بعد: [عدد الدقائق] دقيقة

🧭 الاتجاه: [Bullish / Bearish / Consolidation]
📈 الاتجاه القصير: [Bullish / Bearish]

Market Structure
[شرح مختصر لبنية السوق والقمم والقيعان]

Momentum
[شرح مختصر لقوة الحركة]

Price Action
[شرح مختصر لسلوك الشموع]

شمعة التأكيد
[نوع شمعة التأكيد إذا كانت موجودة]

السبب
[سبب مختصر ومباشر لاختيار CALL أو PUT]

============================================================
قواعد صارمة
============================================================

- لا تفترض M1.
- لا تفرض فريمًا معينًا.
- حدد الفريم من Screenshot إذا كان ظاهرًا.
- لا تخترع الفريم إذا لم يكن واضحًا.
- لا تستخدم أي استراتيجية غير المذكورة.
- لا تضف Support/Resistance كقسم مستقل في النتيجة.
- لا تضف MACD.
- لا تضف Stochastic.
- لا تضف Fibonacci.
- لا تضف ZigZag.
- لا تضف أي مؤشر آخر.
- لا تستخدم NO SIGNAL.
- لا تستخدم NEUTRAL.
- لا تخترع RSI.
- لا تخترع EMA.
- لا تخترع اسم الأصل.
- لا تخترع مستويات سعرية غير ظاهرة.
- لا تدخل مباشرة عند الدعم أو المقاومة.
- انتظر تأكيد الشموع.
- لا تجعل الدخول دائمًا في الدقيقة التالية.
- حدد عدد الدقائق المناسب للدخول بناءً على التحليل.
- يمكن اختيار 1 أو 2 أو 3 دقائق أو أكثر إذا كان الشارت يحتاج ذلك.
- لا تعطِ وقت انتهاء.
- لا تقل إن الصفقة مضمونة.
- نسبة الثقة بين 50% و95%.
- لا تضع أكثر من اتجاه واحد.
- لا تضع أكثر من وقت دخول واحد.
- يجب أن تذكر "الدخول بعد: X دقيقة" بوضوح.
"""


# ============================================================
# TEMPORARY GEMINI ERROR
# ============================================================

def is_temporary_gemini_error(error):

    text = str(error).upper()

    temporary_codes = [
        "429",
        "500",
        "502",
        "503",
        "504",
        "UNAVAILABLE",
        "RESOURCE_EXHAUSTED",
        "INTERNAL",
        "OVERLOADED",
        "HIGH DEMAND",
    ]

    return any(
        code in text
        for code in temporary_codes
    )


# ============================================================
# GEMINI SINGLE REQUEST
# ============================================================

def generate_with_model(
    model_name,
    image_part
):

    print(
        f"Trying Gemini model: {model_name}"
    )

    response = client.models.generate_content(
        model=model_name,
        contents=[
            ANALYSIS_PROMPT,
            image_part
        ]
    )

    if not response or not response.text:
        raise RuntimeError(
            f"{model_name} returned an empty response"
        )

    print(
        f"Gemini success: {model_name}"
    )

    return response.text.strip()


# ============================================================
# IMAGE ANALYSIS
# ============================================================

async def analyze_chart(image_bytes):

    image = Image.open(
        io.BytesIO(image_bytes)
    ).convert("RGB")

    max_size = 1600

    if (
        image.width > max_size
        or image.height > max_size
    ):
        image.thumbnail(
            (max_size, max_size)
        )

    output = io.BytesIO()

    image.save(
        output,
        format="JPEG",
        quality=92
    )

    output.seek(0)

    image_part = types.Part.from_bytes(
        data=output.getvalue(),
        mime_type="image/jpeg"
    )

    # ========================================================
    # ترتيب النماذج
    # ========================================================

    models_to_try = []

    # النموذج الموجود في Render أولًا
    if GEMINI_MODEL:
        models_to_try.append(
            GEMINI_MODEL
        )

    # ثم النماذج الاحتياطية
    for model in GEMINI_FALLBACK_MODELS:

        if model not in models_to_try:
            models_to_try.append(
                model
            )

    last_error = None

    for model_name in models_to_try:

        try:

            return generate_with_model(
                model_name,
                image_part
            )

        except Exception as e:

            last_error = e

            print(
                f"Gemini error on {model_name}: {e}"
            )

            # إذا كان خطأ مؤقتًا:
            # انتقل للنموذج التالي.
            if is_temporary_gemini_error(e):

                print(
                    f"Temporary error. "
                    f"Trying next model..."
                )

                continue

            # أخطاء مثل API key / invalid model
            # لا داعي لتجربة باقي النماذج إذا كان
            # الخطأ غير مؤقت.
            raise

    raise RuntimeError(
        "All Gemini models failed.\n\n"
        + str(last_error)
    )


# ============================================================
# EXTRACT ENTRY MINUTES
# ============================================================

def extract_entry_minutes(text):

    if not text:
        return 1

    lower = text.lower()

    # البحث عن العبارة التي طلبنا من Gemini إخراجها
    # حتى لا نأخذ رقمًا آخر من نسبة الثقة أو الفريم.

    import re

    patterns = [
        r"الدخول\s*بعد\s*[:：]?\s*(\d+)\s*دقيقة",
        r"الدخول\s*بعد\s*[:：]?\s*(\d+)\s*دقائق",
        r"entry\s*after\s*[:：]?\s*(\d+)\s*minute",
        r"entry\s*after\s*[:：]?\s*(\d+)\s*minutes",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            lower
        )

        if match:

            minutes = int(
                match.group(1)
            )

            # السماح من 1 إلى 10 دقائق
            # لأن Gemini قد يرى أن السوق يحتاج
            # وقتًا أطول.
            if minutes < 1:
                return 1

            if minutes > 10:
                return 10

            return minutes

    # fallback إذا لم يكتب Gemini العبارة
    # بوضوح.

    if "3 دقائق" in text or "3 دقيقة" in text:
        return 3

    if "2 دقائق" in text or "2 دقيقة" in text:
        return 2

    if "1 دقيقة" in text or "دقيقة واحدة" in text:
        return 1

    if (
        "3 minutes" in lower
        or "3 minute" in lower
    ):
        return 3

    if (
        "2 minutes" in lower
        or "2 minute" in lower
    ):
        return 2

    if (
        "1 minute" in lower
        or "one minute" in lower
    ):
        return 1

    return 1


# ============================================================
# CLEAN RESULT
# ============================================================

def clean_result(text):

    lines = text.splitlines()

    cleaned = []

    for line in lines:

        # Gemini قد يكتب وقتًا حقيقيًا.
        # نحذفه لأن Python هو الذي يحسبه
        # حسب توقيت Quotex المطلوب.
        if "وقت الدخول" in line:
            continue

        if "entry time" in line.lower():
            continue

        cleaned.append(line)

    return "\n".join(
        cleaned
    ).strip()


# ============================================================
# PHOTO
# ============================================================

async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not update.message:
        return

    if not is_owner(
        update.effective_user.id
    ):
        return

    if not update.message.photo:
        return

    processing = await update.message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:

        telegram_file = (
            await update.message
            .photo[-1]
            .get_file()
        )

        image_bytes = (
            await telegram_file
            .download_as_bytearray()
        )

        result = await analyze_chart(
            bytes(image_bytes)
        )

        # Gemini يحدد عدد الدقائق
        minutes_ahead = extract_entry_minutes(
            result
        )

        # Python يحسب وقت الدخول
        entry_time = calculate_entry_time(
            minutes_ahead
        )

        result = clean_result(
            result
        )

        final_result = (
            result
            + "\n\n"
            + f"⏱️ وقت الدخول: {entry_time}"
        )

        await processing.edit_text(
            final_result
        )

    except Exception as e:

        error = str(e)

        if len(error) > 1500:
            error = error[:1500]

        await processing.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            + error
        )


# ============================================================
# WIN
# ============================================================

async def win_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_owner(
        update.effective_user.id
    ):
        return

    stats["win"] += 1

    total = (
        stats["win"]
        + stats["loss"]
    )

    win_rate = (
        stats["win"] / total * 100
        if total > 0
        else 0
    )

    await update.message.reply_text(
        "✅ WIN\n\n"
        f"🟢 WIN: {stats['win']}\n"
        f"🔴 LOSS: {stats['loss']}\n"
        f"📊 الصفقات: {total}\n"
        f"📈 Win Rate: {win_rate:.1f}%"
    )


# ============================================================
# LOSS
# ============================================================

async def loss_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_owner(
        update.effective_user.id
    ):
        return

    stats["loss"] += 1

    total = (
        stats["win"]
        + stats["loss"]
    )

    win_rate = (
        stats["win"] / total * 100
        if total > 0
        else 0
    )

    await update.message.reply_text(
        "❌ LOSS\n\n"
        f"🟢 WIN: {stats['win']}\n"
        f"🔴 LOSS: {stats['loss']}\n"
        f"📊 الصفقات: {total}\n"
        f"📈 Win Rate: {win_rate:.1f}%"
    )


# ============================================================
# STATS
# ============================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_owner(
        update.effective_user.id
    ):
        return

    total = (
        stats["win"]
        + stats["loss"]
    )

    win_rate = (
        stats["win"] / total * 100
        if total > 0
        else 0
    )

    await update.message.reply_text(
        "📊 إحصائيات الصفقات\n\n"
        f"🟢 WIN: {stats['win']}\n"
        f"🔴 LOSS: {stats['loss']}\n"
        f"📊 المجموع: {total}\n"
        f"📈 Win Rate: {win_rate:.1f}%"
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_owner(
        update.effective_user.id
    ):
        return

    await update.message.reply_text(
        "🤖 ZinoQuotexSignalAI جاهز.\n\n"
        "📸 أرسل Screenshot للشارت."
    )


# ============================================================
# HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_owner(
        update.effective_user.id
    ):
        return

    await update.message.reply_text(
        "📸 أرسل Screenshot للشارت للتحليل.\n\n"
        "/win — تسجيل WIN\n"
        "/loss — تسجيل LOSS\n"
        "/stats — إحصائيات الصفقات"
    )


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"ZinoQuotexSignalAI is running."
        )

    def log_message(
        self,
        format,
        *args
    ):
        return


def start_health_server():

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

    print(
        f"Health server running on port {port}"
    )

    server.serve_forever()


# ============================================================
# ERROR
# ============================================================

async def error_handler(
    update,
    context
):

    print(
        "ERROR:",
        context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=========================================="
    )

    print(
        "ZinoQuotexSignalAI"
    )

    print(
        "Smart Price Action Analysis"
    )

    print(
        "=========================================="
    )

    print(
        f"Primary Gemini model: {GEMINI_MODEL}"
    )

    print(
        "Fallback models:"
    )

    for model in GEMINI_FALLBACK_MODELS:
        print(
            f" - {model}"
        )

    health_thread = threading.Thread(
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
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "win",
            win_command
        )
    )

    application.add_handler(
        CommandHandler(
            "loss",
            loss_command
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats_command
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "Bot is running..."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
