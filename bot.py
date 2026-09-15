import os
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from google import genai
from google.genai import types
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
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
    "gemini-3.6-flash"
)

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# SESSION STATISTICS
# =========================================================

wins = 0
losses = 0


def get_stats_text():

    total = wins + losses

    if total > 0:
        win_rate = (wins / total) * 100
        loss_rate = (losses / total) * 100
    else:
        win_rate = 0
        loss_rate = 0

    return (
        "📊 إحصائيات الجلسة\n\n"
        f"📈 إجمالي الصفقات: {total}\n"
        f"🟢 WIN: {wins}\n"
        f"🔴 LOSS: {losses}\n\n"
        f"🎯 نسبة النجاح: {win_rate:.1f}%\n"
        f"📉 نسبة الخسارة: {loss_rate:.1f}%"
    )


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
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
        f"🌐 Health server running on port {port}"
    )

    server.serve_forever()


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(update: Update):

    user = update.effective_user

    return (
        user is not None
        and user.id == OWNER_ID
    )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )

        return

    await update.message.reply_text(
        "👋 مرحبًا بك في ZinoQuotexSignalAI\n\n"
        "📸 أرسل صورة واضحة لشارت Quotex.\n"
        "وسأحلل الاتجاه وأعطيك إشارة CALL أو PUT.\n\n"
        "📊 لمعرفة إحصائيات الصفقات:\n"
        "/stats\n\n"
        "🔄 لتصفير الإحصائيات:\n"
        "/reset"
    )


# =========================================================
# /HELP
# =========================================================

async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص."
        )

        return

    await update.message.reply_text(
        "📸 أرسل صورة واضحة للرسم البياني.\n\n"
        "يفضل أن يظهر:\n"
        "• اسم الأصل\n"
        "• الإطار الزمني\n"
        "• الشموع\n"
        "• المؤشرات إن وجدت\n"
        "• Volume إن كان ظاهرًا\n"
        "• أكبر قدر ممكن من حركة السعر\n\n"
        "📊 /stats — إحصائيات الجلسة\n"
        "🔄 /reset — تصفير الإحصائيات"
    )


# =========================================================
# /STATS
# =========================================================

async def stats_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص."
        )

        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🔄 تصفير الإحصائيات",
                callback_data="reset_stats"
            )
        ]
    ]

    await update.message.reply_text(
        get_stats_text(),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# /RESET
# =========================================================

async def reset_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global wins, losses

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص."
        )

        return

    wins = 0
    losses = 0

    await update.message.reply_text(
        "🔄 تم تصفير إحصائيات الجلسة.\n\n"
        "📈 إجمالي الصفقات: 0\n"
        "🟢 WIN: 0\n"
        "🔴 LOSS: 0"
    )


# =========================================================
# WIN / LOSS BUTTONS
# =========================================================

async def result_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global wins, losses

    query = update.callback_query

    if query.from_user.id != OWNER_ID:

        await query.answer(
            "🔒 غير مسموح.",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data

    if data == "win":

        wins += 1
        result_text = "🟢 تم تسجيل الصفقة: WIN"

    elif data == "loss":

        losses += 1
        result_text = "🔴 تم تسجيل الصفقة: LOSS"

    elif data == "reset_stats":

        wins = 0
        losses = 0

        await query.edit_message_text(
            "🔄 تم تصفير إحصائيات الجلسة.\n\n"
            "📈 إجمالي الصفقات: 0\n"
            "🟢 WIN: 0\n"
            "🔴 LOSS: 0"
        )

        return

    else:
        return

    await query.edit_message_reply_markup(
        reply_markup=None
    )

    await query.message.reply_text(
        f"{result_text}\n\n"
        f"{get_stats_text()}"
    )


# =========================================================
# PHOTO ANALYSIS
# =========================================================

async def photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )

        return

    status_message = await update.message.reply_text(
        "🔍 جاري تحليل الشارت..."
    )

    try:

        photo_file = await update.message.photo[-1].get_file()

        image_bytes = await photo_file.download_as_bytearray()

        image = Image.open(
            io.BytesIO(image_bytes)
        )

        image.thumbnail(
            (1400, 1400)
        )

        buffer = io.BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=90
        )

        image_data = buffer.getvalue()

        # =================================================
        # CURRENT ALGERIA TIME
        # =================================================

        algeria_now = datetime.now(
            ZoneInfo("Africa/Algiers")
        )

        current_time = algeria_now.strftime(
            "%H:%M"
        )

        current_date = algeria_now.strftime(
            "%Y-%m-%d"
        )

        # =================================================
        # FINAL ANALYSIS PROMPT
        # =================================================

        prompt = f"""
أنت محلل تقني محترف متخصص في تحليل صور شارتات التداول.

حلل صورة الشارت المرفقة فقط.
لا تخترع أي بيانات أو مؤشرات أو Volume أو معلومات غير ظاهرة بوضوح في الصورة.

هدفك هو تحديد السيناريو الأقوى لحركة السعر القادمة، وليس مجرد وصف ما حدث.

━━━━━━━━━━━━━━━━━━
📊 1. تحديد الشارت
━━━━━━━━━━━━━━━━━━

حدد إذا كان ظاهرًا:
- اسم الأصل.
- الإطار الزمني Timeframe.
- السعر الحالي.
- المؤشرات الموجودة.
- Volume.

لا تفترض أي معلومة غير ظاهرة.

━━━━━━━━━━━━━━━━━━
🏗️ 2. Market Structure
━━━━━━━━━━━━━━━━━━

حلل:

- Higher High / Higher Low.
- Lower High / Lower Low.
- Break of Structure.
- CHoCH / Market Structure Shift إذا كان واضحًا.
- قوة الاتجاه الرئيسي.
- هل الاتجاه مستمر أم بدأ يفقد قوته؟

راقب خصوصًا فشل السعر في تكوين قمة أو قاع جديد كما كان يفعل سابقًا.

━━━━━━━━━━━━━━━━━━
💧 3. Liquidity
━━━━━━━━━━━━━━━━━━

ابحث عن:

- Liquidity Sweep.
- Stop Hunt.
- Fake Breakout.
- رفض السعر بعد أخذ السيولة.
- انتقال محتمل للسيولة بين المشترين والبائعين.

لا تعتبر أي Wick عشوائي Liquidity Sweep إلا إذا كان سلوك السعر يدعمه.

━━━━━━━━━━━━━━━━━━
⚡ 4. Momentum
━━━━━━━━━━━━━━━━━━

قيّم:

- قوة الحركة الحالية.
- تسارع أو تباطؤ السعر.
- سيطرة المشترين أو البائعين.
- علامات Exhaustion.
- هل Momentum يؤكد الاتجاه أم يتعارض معه؟

━━━━━━━━━━━━━━━━━━
🕯️ 5. Candle Psychology
━━━━━━━━━━━━━━━━━━

لا تنظر إلى لون الشمعة فقط.

حلل:

- حجم الجسم.
- طول الـWick.
- مكان الإغلاق.
- العلاقة مع الشموع السابقة.
- سرعة الحركة.
- الرفض السعري.
- ضغط المشترين والبائعين.
- التردد.
- Engulfing.
- Pin Bar / Rejection.
- الشموع القوية بعد Liquidity Sweep.

حاول فهم سيكولوجية السوق:

هل المشترون يسيطرون؟
هل البائعون يسيطرون؟
هل أحد الطرفين يفقد السيطرة؟
هل يوجد رفض قوي؟
هل توجد علامات Trap؟
هل الحركة استمرار أم بداية انعكاس؟

لا تعتمد على شمعة واحدة فقط.

━━━━━━━━━━━━━━━━━━
🔄 6. التحول المبكر في سلوك السعر
━━━━━━━━━━━━━━━━━━

ابحث عن علامات مبكرة على تغير سلوك السوق:

- ضعف الاتجاه الحالي.
- فشل في تكوين قمة أعلى أو قاع أدنى.
- تغير تسلسل القمم والقيعان.
- Liquidity Sweep ثم رفض قوي.
- CHoCH أو Market Structure Shift.
- تغير Momentum قبل تغير الاتجاه.
- شموع قوية عكس الاتجاه بعد ظهور الضعف.
- فشل متكرر في مواصلة الاتجاه.
- انتقال السيطرة من المشترين للبائعين أو العكس.

لا تعتبر علامة واحدة كافية.

يصبح احتمال التحول أقوى عندما تتوافق عدة علامات معًا.

لا تفترض انعكاسًا مؤكدًا بدون Confirmation.

━━━━━━━━━━━━━━━━━━
📊 7. Technical Indicators
━━━━━━━━━━━━━━━━━━

إذا كانت المؤشرات ظاهرة بوضوح، استخدمها كأدلة مساعدة.

افحص المؤشرات الظاهرة مثل:

- Moving Averages.
- RSI.
- MACD.
- Stochastic.
- Bollinger Bands.
- أو أي مؤشر آخر ظاهر.

ابحث عن:

- توافق المؤشرات مع الاتجاه.
- Divergence.
- Crossovers.
- Overbought / Oversold.
- تغير Momentum.

لا تعتمد على مؤشر واحد.

إذا تعارض المؤشر مع Price Action وMarket Structure،
أعطِ الأولوية لسلوك السعر.

لا تخترع أي مؤشر غير ظاهر.

━━━━━━━━━━━━━━━━━━
📐 8. Chart Patterns
━━━━━━━━━━━━━━━━━━

ابحث عن الأنماط الواضحة فقط:

- Double Top / Double Bottom.
- Head and Shoulders.
- Triangles.
- Flags / Pennants.
- Wedges.
- Breakout / Retest.
- Range.
- Reversal Patterns.
- Continuation Patterns.

لا تفترض وجود Pattern بسبب تشابه بسيط.

يجب أن يكون النمط واضحًا ومدعومًا بحركة السعر.

━━━━━━━━━━━━━━━━━━
📦 9. Volume
━━━━━━━━━━━━━━━━━━

إذا كان Volume ظاهرًا في الصورة، استخدمه كعامل تأكيد.

افحص:

- Volume أثناء Breakout.
- Volume أثناء Rejection.
- Volume مع الشموع القوية.
- زيادة أو انخفاض Volume.
- هل Volume يؤكد الحركة؟

Breakout + Volume قوي:
دليل إضافي على قوة الاختراق.

Breakout + Volume ضعيف:
احتمال Fake Breakout أعلى.

حركة قوية + Volume متزايد:
Momentum أقوى.

حركة كبيرة + Volume ضعيف:
تعامل معها بحذر.

إذا لم يكن Volume ظاهرًا، اكتب:
"غير ظاهر"

ولا تخترع أي قراءة له.

مهم:
إذا كان الشارت OTC أو لا يمثل Volume حقيقيًا بشكل موثوق،
لا تجعل Volume العامل الرئيسي في القرار.

━━━━━━━━━━━━━━━━━━
🎯 10. قرار الإشارة
━━━━━━━━━━━━━━━━━━

اجمع الأدلة من:

Price Action
+
Candle Psychology
+
Market Structure
+
Liquidity
+
Momentum
+
Indicators
+
Chart Patterns
+
Volume إذا كان ظاهرًا

لا تعتمد على عامل واحد.

الأولوية عند وجود تعارض:

1. Price Action + Candle Psychology
2. Market Structure
3. Liquidity
4. Momentum
5. Volume
6. Chart Patterns
7. Indicators

هذه ليست معادلة رياضية ثابتة،
بل ترتيب للأهمية عند تعارض الأدلة.

اختر السيناريو الأقوى:

🟢 CALL / UP = صعود
🔴 PUT / DOWN = هبوط

لا تستخدم NO SIGNAL.

لكن لا تعطِ إشارة عشوائية.

إذا كانت الأدلة متضاربة،
اختر السيناريو الذي تدعمه الأدلة الأقوى
وخفّض نسبة الثقة.

لا ترفع الثقة لمجرد إعطاء رقم مرتفع.

لا تستخدم ثقة 100%.

━━━━━━━━━━━━━━━━━━
⏱️ 11. وقت الدخول والمدة
━━━━━━━━━━━━━━━━━━

توقيت التحليل الحالي هو:

{current_time}

بتاريخ:

{current_date}

والمنطقة الزمنية:

Africa/Algiers

استخدم هذا الوقت كمرجع.

لا تخترع توقيتًا بعيدًا.

وقت الدخول يجب أن يكون قريبًا من وقت التحليل
ومرتبطًا باكتمال شمعة التأكيد.

لا تدخل أثناء تكون شمعة التأكيد.

لا تفترض أن الشارت M1.

استخدم الـTimeframe الظاهر في الصورة.

حدد مدة صفقة مناسبة للـTimeframe وقوة الحركة.

━━━━━━━━━━━━━━━━━━
📋 12. OUTPUT
━━━━━━━━━━━━━━━━━━

أخرج النتيجة بهذا الشكل:

📊 الأصل: [اسم الأصل]
📊 الإطار الزمني: [Timeframe]

🧭 الاتجاه الرئيسي: [صاعد / هابط / متذبذب]
📈 الاتجاه القصير: [صاعد / هابط]

━━━━━━━━━━━━━━━━━━

🏗️ Market Structure
[تحليل مختصر]

💧 Liquidity
[تحليل مختصر]

⚡ Momentum
[تحليل مختصر]

📈 Technical Price Action
[تحليل مختصر]

🕯️ Candle Psychology
[تحليل مختصر]

🔄 التحول في سلوك السعر
[تحليل مختصر]

📊 المؤشرات
[المؤشرات الظاهرة فقط]

📐 Chart Pattern
[النمط إن كان واضحًا، وإلا:
لا يوجد نمط واضح]

📦 Volume
[التحليل إذا كان ظاهرًا،
وإلا: غير ظاهر]

🕯️ شمعة التأكيد
[سبب اختيارها]

🧠 السبب
[الخلاصة الفنية المختصرة]

━━━━━━━━━━━━━━━━━━

🎯 الإشارة: [🟢 صعود (CALL / UP) أو 🔴 هبوط (PUT / DOWN)]
🕐 وقت الدخول: [HH:MM]
⏳ مدة الصفقة: [المدة]
📊 نسبة الثقة: [XX%]

قواعد نهائية:

- إشارة واحدة فقط.
- لا تستخدم NO SIGNAL.
- لا تخترع بيانات غير ظاهرة.
- لا تعتمد على مؤشر واحد.
- لا تعتمد على شمعة واحدة.
- لا تجعل UP وDOWN متساويين بشكل مصطنع.
- لا ترفع نسبة الثقة بدون أدلة.
- الأولوية لجودة نقطة الدخول.
- اجعل التحليل واضحًا ومباشرًا.
- لا تضف Support/Resistance في التقرير النهائي إلا إذا كان ضروريًا جدًا لتفسير القرار.
- لا تقدم ضمانًا للربح.
"""

        # =================================================
        # GEMINI ANALYSIS
        # =================================================

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(
                    data=image_data,
                    mime_type="image/jpeg"
                ),
                prompt
            ]
        )

        result = response.text

        keyboard = [
            [
                InlineKeyboardButton(
                    "🟢 WIN",
                    callback_data="win"
                ),
                InlineKeyboardButton(
                    "🔴 LOSS",
                    callback_data="loss"
                )
            ]
        ]

        await status_message.edit_text(
            result,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    except Exception as e:

        print(
            f"BOT ERROR: {repr(e)}"
        )

        await status_message.edit_text(
            "❌ حدث خطأ أثناء تحليل الصورة.\n"
            "حاول إرسال الشارت مرة أخرى."
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "🚀 Starting ZinoQuotexSignalAI..."
    )

    health_thread = Thread(
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
            help_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "reset",
            reset_cmd
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            result_callback,
            pattern="^(win|loss|reset_stats)$"
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo
        )
    )

    print(
        "🤖 Telegram bot starting..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
