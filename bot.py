import os
import io
import json
import re
from datetime import datetime
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

============================================================

ENVIRONMENT

============================================================

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

client = genai.Client(api_key=GEMINI_API_KEY)

============================================================

TRADE MEMORY

============================================================

trades = []
trade_counter = 0

============================================================

HEALTH SERVER FOR RENDER

============================================================

class HealthHandler(BaseHTTPRequestHandler):
def do_GET(self):
self.send_response(200)
self.end_headers()
self.wfile.write(b"ZinoQuotexSignalAI is running")

def log_message(self, format, *args):
    return

def run_health_server():
port = int(os.environ.get("PORT", 10000))
server = HTTPServer(("0.0.0.0", port), HealthHandler)
server.serve_forever()

Thread(target=run_health_server, daemon=True).start()

============================================================

STRATEGY PROMPT

============================================================

SYSTEM_PROMPT = """
أنت ZinoQuotexSignalAI، محلل فني متخصص في تحليل صور شارت التداول.

مهمتك تحليل الشارت الظاهر في الصورة فقط وإعطاء اتجاه تداول واضح
CALL (UP) أو PUT (DOWN) بناءً على توافق عدة عوامل.

هذه الاستراتيجية مبنية على:

1. Parabolic SAR
2. Moving Average 5
3. Moving Average 8
4. Moving Average 13
5. ADX / DI

إعدادات المؤشرات:

PARABOLIC SAR:

- استخدم نقاط SAR الظاهرة على الشارت.
- SAR أسفل السعر = ميل صاعد.
- SAR أعلى السعر = ميل هابط.
- تغير مكان SAR مهم، لكنه ليس تأكيدًا منفردًا.

MOVING AVERAGES:

- MA 5 = أخضر
- MA 8 = أصفر
- MA 13 = أحمر

راقب:

- ترتيب المتوسطات.
- التقاطعات.
- اتجاه الميل.
- المسافة بينها.
- هل المتوسطات مفتوحة ومتباعدة أم متداخلة.

ترتيب صاعد:
MA5 > MA8 > MA13

ترتيب هابط:
MA5 < MA8 < MA13

ممنوع اعتبار تقاطع واحد وحده إشارة مؤكدة.

ADX / DI:

- DI Length = 9
- ADX Smoothing = 7

الخطوط:

- DI+ = أخضر
- DI- = برتقالي
- ADX = أحمر

عندما تكون الخطوط الثلاثة مفتوحة ومتباعدة وتتحرك في اتجاه واضح،
اعتبر ذلك دليلًا على وجود حركة منظمة وقوية.

عندما تبدأ الخطوط بالتقاطع والتداخل،
اعتبر ذلك WARNING لاحتمال تغير الاتجاه أو ارتداد أو انعكاس.

لكن لا تعتبر التقاطع وحده انعكاسًا مؤكدًا.

يجب مقارنة ADX/DI مع:

- Moving Averages
- Parabolic SAR
- Market Structure
- Momentum
- Price Action
- Confirmation Candle

========================
MARKET STRUCTURE

الاتجاه الصاعد:
Higher High + Higher Low

الاتجاه الهابط:
Lower High + Lower Low

راقب:

- Break of Structure
- Change of Character
- استمرار الاتجاه
- الارتداد
- القمم والقيعان

========================
MOMENTUM

BULLISH عندما:

- السعر يتحرك للأعلى بقوة
- MA5 فوق MA8 فوق MA13
- DI+ أقوى من DI-
- ADX يدعم قوة الحركة
- SAR أسفل السعر

BEARISH عندما:

- السعر يتحرك للأسفل بقوة
- MA5 تحت MA8 تحت MA13
- DI- أقوى من DI+
- ADX يدعم قوة الحركة
- SAR أعلى السعر

========================
REVERSAL DETECTION

إذا بدأت خطوط ADX/DI بالتقاطع:

لا تعطي انعكاسًا مباشرة.

ابحث عن توافق إضافي:

ADX/DI crossover
+
MA5/MA8/MA13 crossover
+
تغير SAR
+
تغير Market Structure
+
Confirmation Candle

كلما اجتمعت هذه العوامل، يصبح احتمال تغير الاتجاه أقوى.

========================
CALL CONDITIONS

CALL عندما يكون هناك توافق صاعد واضح، مثل:

- MA5 > MA8 > MA13
- المتوسطات مائلة للأعلى
- المتوسطات بدأت تتباعد
- DI+ أقوى من DI-
- ADX يدعم قوة الحركة
- SAR أسفل السعر
- Market Structure صاعد
- Momentum صاعد
- Confirmation Candle صاعدة

لا يشترط وجود جميع العناصر، لكن يجب أن تكون الأغلبية متوافقة.

========================
PUT CONDITIONS

PUT عندما يكون هناك توافق هابط واضح، مثل:

- MA5 < MA8 < MA13
- المتوسطات مائلة للأسفل
- المتوسطات بدأت تتباعد
- DI- أقوى من DI+
- ADX يدعم قوة الحركة
- SAR أعلى السعر
- Market Structure هابط
- Momentum هابط
- Confirmation Candle هابطة

لا يشترط وجود جميع العناصر، لكن يجب أن تكون الأغلبية متوافقة.

========================
AVOID BAD ENTRIES

خفض الثقة عندما:

- MA5/MA8/MA13 متشابكة.
- ADX/DI متداخلة.
- SAR يتغير باستمرار.
- السعر يتحرك بشكل جانبي.
- المؤشرات متناقضة.
- لا توجد شمعة تأكيد واضحة.

ممنوع إعطاء ثقة عالية عندما تكون الأدلة متضاربة.

========================
IMPORTANT

ممنوع اختراع أي معلومة غير ظاهرة في الصورة.

إذا لم يظهر:

- اسم الأصل → اكتب غير واضح
- الإطار الزمني → اكتب غير واضح

لا تدّعي رؤية مؤشر غير موجود.

لا تعتمد على لون المؤشر فقط.
اعتمد على موقعه واتجاهه وعلاقته ببقية المؤشرات.

========================
SIGNAL RULE

يجب دائمًا اختيار الاتجاه الذي تدعمه الأدلة الأقوى:

CALL أو PUT

لا تستخدم:
NO SIGNAL

إذا كانت الأدلة ضعيفة:
اختر الاتجاه الأكثر دعمًا، لكن اخفض نسبة الثقة.

نسبة الثقة تقدير تحليلي وليست ضمانًا للنتيجة.

لا تكتب 90% أو 95% بدون توافق قوي جدًا.

========================
OUTPUT FORMAT

استخدم هذا الشكل بالضبط:

🎯 الإشارة: 🟢 CALL (UP) XX%

📊 نسبة الثقة: XX%
📊 الأصل: [اسم الأصل]
📊 الإطار الزمني: [M1/M5/...]
🧭 الاتجاه: [Bullish/Bearish]
📈 الاتجاه القصير: [Bullish/Bearish]

━━━━━━━━━━━━━━━━━━
Market Structure
━━━━━━━━━━━━━━━━━━
[تحليل مختصر]

━━━━━━━━━━━━━━━━━━
ADX / DI
━━━━━━━━━━━━━━━━━━
[حالة DI+ وDI- وADX]

━━━━━━━━━━━━━━━━━━
Moving Averages
━━━━━━━━━━━━━━━━━━
[تحليل MA5 / MA8 / MA13]

━━━━━━━━━━━━━━━━━━
Parabolic SAR
━━━━━━━━━━━━━━━━━━
[تحليل SAR]

━━━━━━━━━━━━━━━━━━
Momentum
━━━━━━━━━━━━━━━━━━
[تحليل القوة]

━━━━━━━━━━━━━━━━━━
Price Action
━━━━━━━━━━━━━━━━━━
[تحليل حركة السعر]

━━━━━━━━━━━━━━━━━━
شمعة التأكيد
━━━━━━━━━━━━━━━━━━
[نوع شمعة التأكيد]

━━━━━━━━━━━━━━━━━━
السبب
━━━━━━━━━━━━━━━━━━
• السبب الأول
• السبب الثاني
• السبب الثالث

========================
TRADE RESULT

لا تسجل WIN أو LOSS أثناء تحليل الصورة.

بعد انتهاء الصفقة، يمكن للمستخدم إرسال:

WIN

أو:

LOSS

عندها يتم تسجيل النتيجة للصفقة الأخيرة.

========================
PERFORMANCE

بعد تسجيل النتيجة اعرض:

📊 إجمالي الصفقات: XX
✅ WIN: XX
❌ LOSS: XX
🎯 WIN RATE: XX%
📈 أفضل سلسلة WIN: XX
📉 أطول سلسلة LOSS: XX

WIN RATE =
WIN ÷ إجمالي الصفقات × 100

لا تغيّر الإشارة الأصلية بعد ظهور النتيجة.
"""

============================================================

OWNER CHECK

============================================================

def is_owner(update: Update):
user = update.effective_user

if not user:
    return False

return user.id == OWNER_ID

============================================================

START

============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

if not is_owner(update):
    await update.message.reply_text("🔒 هذا البوت خاص.")
    return

await update.message.reply_text(
    "🔥 ZinoQuotexSignalAI جاهز\n\n"
    "📸 أرسل صورة الشارت M1.\n"
    "وسأحلل:\n"
    "• ADX / DI\n"
    "• MA 5 / 8 / 13\n"
    "• Parabolic SAR\n"
    "• Market Structure\n"
    "• Momentum\n"
    "• Price Action\n"
    "• Confirmation Candle\n\n"
    "بعد الصفقة أرسل WIN أو LOSS لتسجيل النتيجة."
)

============================================================

HELP

============================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

if not is_owner(update):
    return

await update.message.reply_text(
    "📖 الأوامر:\n\n"
    "/start — تشغيل البوت\n"
    "/help — المساعدة\n"
    "/stats — إحصائيات WIN/LOSS\n\n"
    "📸 أرسل الشارت للتحليل.\n"
    "بعد انتهاء الصفقة أرسل WIN أو LOSS."
)

============================================================

IMAGE ANALYSIS

============================================================

async def analyze_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):

global trade_counter

if not is_owner(update):
    await update.message.reply_text("🔒 غير مصرح لك باستخدام هذا البوت.")
    return

if not update.message.photo:
    return

await update.message.reply_text(
    "🔍 جاري تحليل الشارت...\n"
    "ADX/DI + MA5/8/13 + SAR + Structure + Momentum"
)

try:

    photo = update.message.photo[-1]

    file = await context.bot.get_file(photo.file_id)

    image_bytes = await file.download_as_bytearray()

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Resize image while preserving quality
    image.thumbnail((1600, 1600))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    buffer.seek(0)

    prompt = SYSTEM_PROMPT + """

حلل الصورة الحالية الآن.

ركز على المؤشرات الظاهرة فعليًا في الصورة.

لا تفترض وجود مؤشرات غير ظاهرة.

أعطني إشارة واحدة فقط:
CALL أو PUT.

ثم أعطني التحليل بالشكل المطلوب.
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            prompt,
            image,
        ],
    )

    result = response.text.strip()

    trade_counter += 1

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    trade = {
        "id": trade_counter,
        "time": now,
        "signal": result,
        "result": "PENDING",
    }

    trades.append(trade)

    await update.message.reply_text(
        f"🆔 الصفقة #{trade_counter}\n\n"
        + result
        + "\n\n"
        "⏳ النتيجة: PENDING\n"
        "بعد انتهاء الصفقة أرسل WIN أو LOSS."
    )

except Exception as e:

    await update.message.reply_text(
        "❌ حدث خطأ أثناء التحليل:\n\n"
        + str(e)
    )

============================================================

RECORD WIN / LOSS

============================================================

async def result_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

if not is_owner(update):
    return

text = update.message.text.strip().upper()

if text not in ["WIN", "LOSS"]:
    return

if not trades:
    await update.message.reply_text(
        "⚠️ لا توجد صفقة مسجلة."
    )
    return

# Find latest pending trade
pending_trade = None

for trade in reversed(trades):
    if trade["result"] == "PENDING":
        pending_trade = trade
        break

if not pending_trade:
    await update.message.reply_text(
        "⚠️ لا توجد صفقة Pending."
    )
    return

pending_trade["result"] = text

stats = calculate_stats()

emoji = "✅" if text == "WIN" else "❌"

await update.message.reply_text(
    f"{emoji} تم تسجيل الصفقة #{pending_trade['id']} = {text}\n\n"
    f"📊 إجمالي الصفقات: {stats['total']}\n"
    f"✅ WIN: {stats['wins']}\n"
    f"❌ LOSS: {stats['losses']}\n"
    f"🎯 WIN RATE: {stats['win_rate']:.1f}%\n"
    f"📈 أفضل سلسلة WIN: {stats['best_win_streak']}\n"
    f"📉 أطول سلسلة LOSS: {stats['best_loss_streak']}"
)

============================================================

STATISTICS

============================================================

def calculate_stats():

completed = [
    t for t in trades
    if t["result"] in ["WIN", "LOSS"]
]

wins = sum(
    1 for t in completed
    if t["result"] == "WIN"
)

losses = sum(
    1 for t in completed
    if t["result"] == "LOSS"
)

total = len(completed)

win_rate = (
    wins / total * 100
    if total > 0
    else 0
)

best_win_streak = 0
current_win = 0

best_loss_streak = 0
current_loss = 0

for trade in completed:

    if trade["result"] == "WIN":
        current_win += 1
        current_loss = 0

        best_win_streak = max(
            best_win_streak,
            current_win
        )

    else:
        current_loss += 1
        current_win = 0

        best_loss_streak = max(
            best_loss_streak,
            current_loss
        )

return {
    "total": total,
    "wins": wins,
    "losses": losses,
    "win_rate": win_rate,
    "best_win_streak": best_win_streak,
    "best_loss_streak": best_loss_streak,
}

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

if not is_owner(update):
    return

stats = calculate_stats()

await update.message.reply_text(
    "━━━━━━━━━━━━━━━━━━\n"
    "📊 SESSION RESULTS\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    f"📌 إجمالي الصفقات: {stats['total']}\n"
    f"✅ WIN: {stats['wins']}\n"
    f"❌ LOSS: {stats['losses']}\n"
    f"🎯 WIN RATE: {stats['win_rate']:.1f}%\n"
    f"📈 أفضل سلسلة WIN: {stats['best_win_streak']}\n"
    f"📉 أطول سلسلة LOSS: {stats['best_loss_streak']}"
)

============================================================

TELEGRAM APPLICATION

============================================================

def main():

application = (
    Application.builder()
    .token(BOT_TOKEN)
    .build()
)

application.add_handler(
    CommandHandler("start", start)
)

application.add_handler(
    CommandHandler("help", help_command)
)

application.add_handler(
    CommandHandler("stats", stats_command)
)

application.add_handler(
    MessageHandler(
        filters.PHOTO,
        analyze_chart
    )
)

application.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        result_handler
    )
)

print("🚀 ZinoQuotexSignalAI started")

application.run_polling(
    drop_pending_updates=True
)

if name == "main":
main()
