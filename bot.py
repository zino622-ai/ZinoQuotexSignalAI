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
# Quotex M1 Price Action Analysis
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

client = genai.Client(api_key=GEMINI_API_KEY)

ALGERIA_TZ = ZoneInfo("Africa/Algiers")

# Quotex timezone requested by user: UTC-3
QUOTEX_TZ = ZoneInfo("Etc/GMT+3")

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
    return datetime.now(ALGERIA_TZ).astimezone(QUOTEX_TZ)


def calculate_entry_time(minutes_ahead):
    now = get_current_quotex_time()

    next_candle = (now + timedelta(minutes=1)).replace(
        second=0,
        microsecond=0
    )

    entry = next_candle + timedelta(minutes=minutes_ahead - 1)

    return entry.strftime("%H:%M")


# ============================================================
# GEMINI PROMPT
# ============================================================

ANALYSIS_PROMPT = """
أنت محلل فني متخصص في تحليل شارت Quotex على فريم M1.

حلل Screenshot المرسل فقط، ولا تخترع أي معلومة غير ظاهرة في الصورة.

الاستراتيجيات المسموح لك باستخدامها هي فقط التالية:

============================================================
1. الاتجاه الهابط Downtrend
============================================================

إذا كان السوق هابطًا، ابحث عن:

- Lower High
- Lower Low
- قمم أقل
- قيعان أقل

في الاتجاه الهابط تكون الأولوية لصفقات PUT / DOWN عندما تؤكد الشموع استمرار الهبوط.

============================================================
2. الاتجاه الصاعد Uptrend
============================================================

إذا كان السوق صاعدًا، ابحث عن:

- Higher High
- Higher Low
- قمم أعلى
- قيعان أعلى

وفي الاتجاه الصاعد تكون الأولوية لصفقات CALL / UP عندما تؤكد الشموع استمرار الصعود.

============================================================
3. السوق العرضي Consolidation
============================================================

إذا كان السعر يتحرك أفقيًا بين منطقتين:

- اعتبر السوق Consolidation.
- راقب الحد العلوي والحد السفلي.
- عند الاقتراب من المقاومة ابحث عن فرصة PUT بعد تأكيد شموعي.
- عند الاقتراب من الدعم ابحث عن فرصة CALL بعد تأكيد شموعي.
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

============================================================
5. الشموع
============================================================

ركز بقوة على الشموع عند الافتتاح والإغلاق.

راقب:

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
يجب أن تتوافق مع اتجاه السعر أو منطقة الدعم/المقاومة.

============================================================
7. RSI
============================================================

استخدم RSI إذا كان ظاهرًا بوضوح في Screenshot.

إذا كان RSI فوق 70:
السوق في تشبع شرائي.
ابحث عن فرصة هبوط PUT إذا أكدت الشموع ذلك.

إذا كان RSI تحت 30:
السوق في تشبع بيعي.
ابحث عن فرصة صعود CALL إذا أكدت الشموع ذلك.

لا تخترع قيمة RSI إذا لم تكن ظاهرة.

============================================================
8. EMA 5 و EMA 13
============================================================

استخدم EMA 5 و EMA 13 إذا كانا ظاهرين بوضوح في Screenshot.

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

الإطار الأساسي هو M1.

لكن لا يشترط الدخول في الدقيقة التالية مباشرة.

اختر وقت الدخول حسب قوة التأكيد:

1 دقيقة:
إذا كانت الإشارة قوية جدًا والتأكيد واضح.

2 دقيقة:
إذا كانت الإشارة جيدة ولكن تحتاج السعر وقتًا بسيطًا للتحرك.

3 دقائق:
إذا كان الاتجاه واضحًا لكن السعر يحتاج مساحة للوصول إلى الهدف أو بعد ارتداد متوقع.

اختر واحدًا فقط:

1 دقيقة
أو
2 دقيقة
أو
3 دقائق

وقت الدخول يجب أن يكون بداية الشمعة التي اخترتها.

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
11. ترتيب قوة الأدلة
============================================================

أعطِ الأولوية إلى:

1. الاتجاه وقمم/قيعان السعر.
2. الدعم والمقاومة.
3. إغلاق وافتتاح الشموع.
4. Hammer / Pin Bar.
5. Engulfing.
6. RSI إذا كان ظاهرًا.
7. EMA 5 و EMA 13 إذا كانا ظاهرين.

============================================================
12. النتيجة
============================================================

أخرج النتيجة بهذا الشكل فقط:

🎯 الإشارة: 🟢 CALL (UP) XX%

📊 نسبة الثقة: XX%
📊 الأصل: [اسم الأصل]
📊 الإطار الزمني: 1M
⏱️ وقت الدخول: [الوقت]

🧭 الاتجاه: [Bullish / Bearish / Consolidation]
📈 الاتجاه القصير: [Bullish / Bearish]

Market Structure
[شرح مختصر للقمم والقيعان والاتجاه أو التذبذب]

Momentum
[شرح مختصر لقوة الحركة]

Price Action
[شرح مختصر لسلوك الشموع عند الافتتاح والإغلاق]

شمعة التأكيد
[اذكر نوع شمعة التأكيد إذا كانت موجودة]

السبب
[سبب مختصر ومباشر لاختيار CALL أو PUT]

============================================================
قواعد صارمة
============================================================

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
- وقت الدخول يمكن أن يكون بعد 1 أو 2 أو 3 دقائق.
- لا تعطِ وقت انتهاء.
- لا تقل إن الصفقة مضمونة.
- نسبة الثقة بين 50% و95%.
- لا تضع أكثر من اتجاه واحد.
- لا تضع أكثر من وقت دخول واحد.
"""


# ============================================================
# IMAGE ANALYSIS
# ============================================================

async def analyze_chart(image_bytes):
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    max_size = 1600

    if image.width > max_size or image.height > max_size:
        image.thumbnail((max_size, max_size))

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

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            ANALYSIS_PROMPT,
            image_part
        ]
    )

    if not response or not response.text:
        raise RuntimeError("Gemini returned an empty response")

    return response.text.strip()


# ============================================================
# EXTRACT ENTRY MINUTES
# ============================================================

def extract_entry_minutes(text):
    lower = text.lower()

    if "3 دقائق" in text or "3 دقيقة" in text:
        return 3

    if "2 دقائق" in text or "2 دقيقة" in text:
        return 2

    if "1 دقيقة" in text or "دقيقة واحدة" in text:
        return 1

    if "3 minutes" in lower or "3 minute" in lower:
        return 3

    if "2 minutes" in lower or "2 minute" in lower:
        return 2

    return 1


# ============================================================
# CLEAN RESULT
# ============================================================

def clean_result(text):

    # إزالة أي وقت دخول أعطاه Gemini
    lines = text.splitlines()
    cleaned = []

    for line in lines:
        if "وقت الدخول" in line:
            continue

        cleaned.append(line)

    return "\n".join(cleaned).strip()


# ============================================================
# PHOTO
# ============================================================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    if not update.message:
        return

    if not is_owner(update.effective_user.id):
        return

    if not update.message.photo:
        return

    processing = await update.message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:

        telegram_file = await update.message.photo[-1].get_file()

        image_bytes = await telegram_file.download_as_bytearray()

        result = await analyze_chart(
            bytes(image_bytes)
        )

        minutes_ahead = extract_entry_minutes(result)

        entry_time = calculate_entry_time(
            minutes_ahead
        )

        result = clean_result(result)

        final_result = (
            result
            + f"\n\n⏱️ وقت الدخول: {entry_time}"
        )

        await processing.edit_text(
            final_result
        )

    except Exception as e:

        error = str(e)

        if len(error) > 1000:
            error = error[:1000]

        await processing.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            + error
        )


# ============================================================
# WIN
# ============================================================

async def win_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    if not is_owner(update.effective_user.id):
        return

    stats["win"] += 1

    total = stats["win"] + stats["loss"]

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

async def loss_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    if not is_owner(update.effective_user.id):
        return

    stats["loss"] += 1

    total = stats["win"] + stats["loss"]

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

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    if not is_owner(update.effective_user.id):
        return

    total = stats["win"] + stats["loss"]

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    if not is_owner(update.effective_user.id):
        return

    await update.message.reply_text(
        "🤖 ZinoQuotexSignalAI جاهز.\n\n"
        "📸 أرسل Screenshot للشارت."
    )


# ============================================================
# HELP
# ============================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    if not is_owner(update.effective_user.id):
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

class HealthHandler(BaseHTTPRequestHandler):

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

    def log_message(self, format, *args):
        return


def start_health_server():

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

    print(
        f"Health server running on port {port}"
    )

    server.serve_forever()


# ============================================================
# ERROR
# ============================================================

async def error_handler(update, context):

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
        "Quotex M1 Price Action"
    )

    print(
        "=========================================="
    )

    print(
        f"Gemini model: {GEMINI_MODEL}"
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
