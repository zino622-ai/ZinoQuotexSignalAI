import os
import io
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from PIL import Image
from google import genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

============================================================

SETTINGS

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

TRADE STORAGE

============================================================

trades = []
trade_counter = 0

============================================================

RENDER HEALTH SERVER

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

ANALYSIS PROMPT

============================================================

SYSTEM_PROMPT = """
أنت ZinoQuotexSignalAI، محلل فني متخصص في تحليل صور شارت التداول.

مهمتك تحليل الشارت الظاهر في الصورة فقط وإعطاء اتجاه تداول واضح:
CALL (UP) أو PUT (DOWN).

لا تعتمد على مؤشر واحد.
يجب دمج جميع الأدلة الظاهرة في الصورة.

المؤشرات الأساسية:

1. Parabolic SAR
2. Moving Average 5
3. Moving Average 8
4. Moving Average 13
5. ADX / DI

PARABOLIC SAR:

- SAR أسفل السعر = ميل صاعد.
- SAR أعلى السعر = ميل هابط.
- انتقال SAR من أعلى السعر إلى أسفله أو العكس مهم.
- لا تستخدم SAR وحده لاتخاذ القرار.

MOVING AVERAGES:

MA 5 = أخضر
MA 8 = أصفر
MA 13 = أحمر

راقب:

- ترتيب المتوسطات.
- التقاطعات.
- اتجاه الميل.
- المسافة بينها.
- هل المتوسطات مفتوحة ومتباعدة؟
- هل المتوسطات متداخلة ومتشابكة؟

الترتيب الصاعد:

MA5 > MA8 > MA13

الترتيب الهابط:

MA5 < MA8 < MA13

التقاطع وحده ليس إشارة مؤكدة.

ADX / DI:

DI Length = 9
ADX Smoothing = 7

الخطوط:

DI+ = أخضر
DI- = برتقالي
ADX = أحمر

عندما تكون الخطوط الثلاثة مفتوحة ومتباعدة وتتحرك في اتجاه واضح،
اعتبر ذلك دليلًا على وجود حركة منظمة وقوية.

عندما تبدأ الخطوط بالتقاطع والتداخل،
اعتبر ذلك تحذيرًا من احتمال تغير الاتجاه أو ارتداد أو انعكاس.

لكن لا تعتبر التقاطع وحده انعكاسًا مؤكدًا.

MARKET STRUCTURE:

الاتجاه الصاعد:

Higher High
Higher Low

الاتجاه الهابط:

Lower High
Lower Low

راقب:

- استمرار الاتجاه.
- Break of Structure.
- تغير البنية.
- القمم والقيعان.
- الارتداد.

MOMENTUM:

BULLISH عندما:

- السعر يتحرك للأعلى بقوة.
- MA5 فوق MA8 فوق MA13.
- DI+ أقوى من DI-.
- ADX يدعم قوة الحركة.
- SAR أسفل السعر.

BEARISH عندما:

- السعر يتحرك للأسفل بقوة.
- MA5 تحت MA8 تحت MA13.
- DI- أقوى من DI+.
- ADX يدعم قوة الحركة.
- SAR أعلى السعر.

REVERSAL:

إذا بدأت خطوط ADX/DI بالتقاطع والتداخل،
لا تعتبر ذلك انعكاسًا مباشرة.

ابحث عن تأكيد من:

ADX/DI
+
MA5/MA8/MA13
+
Parabolic SAR
+
Market Structure
+
Price Action
+
Confirmation Candle

كلما اجتمعت الأدلة، زادت قوة احتمال تغير الاتجاه.

CALL:

أعط CALL عندما يكون هناك توافق صاعد واضح:

- MA5 فوق MA8 فوق MA13.
- المتوسطات مائلة للأعلى.
- المتوسطات بدأت تتباعد.
- DI+ أقوى من DI-.
- ADX يدعم الحركة.
- SAR أسفل السعر.
- Market Structure صاعد.
- Momentum صاعد.
- شمعة تأكيد صاعدة.

PUT:

أعط PUT عندما يكون هناك توافق هابط واضح:

- MA5 تحت MA8 تحت MA13.
- المتوسطات مائلة للأسفل.
- المتوسطات بدأت تتباعد.
- DI- أقوى من DI+.
- ADX يدعم الحركة.
- SAR أعلى السعر.
- Market Structure هابط.
- Momentum هابط.
- شمعة تأكيد هابطة.

تجنب الإشارة القوية عندما:

- المتوسطات متشابكة.
- ADX/DI متداخلة.
- SAR يتغير باستمرار.
- السوق جانبي.
- المؤشرات متناقضة.
- لا توجد شمعة تأكيد واضحة.

ممنوع استخدام NO SIGNAL.

يجب اختيار الاتجاه الذي تدعمه الأدلة الأقوى:

CALL أو PUT.

إذا كانت الأدلة ضعيفة، اختر الاتجاه الأكثر دعمًا
ولكن اخفض نسبة الثقة.

CONFIDENCE:

نسبة الثقة تقدير تحليلي وليست ضمانًا للنتيجة.

ثقة مرتفعة:
عندما تتفق معظم المؤشرات مع بعضها.

ثقة متوسطة:
عندما تكون أغلب المؤشرات متفقة مع وجود بعض التناقض.

ثقة منخفضة:
عندما تكون الأدلة متضاربة أو السوق متذبذبًا.

لا تكتب 90% أو 95% بدون توافق قوي جدًا.

IMPORTANT:

لا تخترع معلومات غير ظاهرة في الصورة.

إذا لم يظهر اسم الأصل:
اكتب غير واضح.

إذا لم يظهر الإطار الزمني:
اكتب غير واضح.

لا تدّعي رؤية مؤشر غير موجود.

لا تعتمد على لون المؤشر فقط.
اعتمد على موقعه واتجاهه وعلاقته ببقية المؤشرات.

OUTPUT FORMAT:

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
[تحليل الخطوط الثلاثة]

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

IMPORTANT TRADE RULE:

بعد تحليل الصورة، سجل الصفقة على أنها PENDING.

لا تسجل WIN أو LOSS من نفسك.

النتيجة يتم تسجيلها فقط عندما يرسل المستخدم:

WIN

أو:

LOSS
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
    "📸 أرسل صورة الشارت M1 للتحليل.\n\n"
    "بعد انتهاء الصفقة أرسل WIN أو LOSS."
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
    "/stats — الإحصائيات\n\n"
    "📸 أرسل صورة الشارت للتحليل.\n"
    "بعد انتهاء الصفقة أرسل WIN أو LOSS."
)

============================================================

CHART ANALYSIS

============================================================

async def analyze_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):

global trade_counter

if not is_owner(update):
    await update.message.reply_text(
        "🔒 غير مصرح لك باستخدام هذا البوت."
    )
    return

await update.message.reply_text(
    "🔍 جاري تحليل الشارت...\n\n"
    "ADX/DI + MA5/8/13 + SAR + Structure + Momentum"
)

try:

    photo = update.message.photo[-1]

    telegram_file = await context.bot.get_file(
        photo.file_id
    )

    image_bytes = await telegram_file.download_as_bytearray()

    image = Image.open(
        io.BytesIO(image_bytes)
    ).convert("RGB")

    image.thumbnail((1600, 1600))

    buffer = io.BytesIO()

    image.save(
        buffer,
        format="JPEG",
        quality=95
    )

    buffer.seek(0)

    prompt = SYSTEM_PROMPT + """

حلل الصورة الحالية الآن.

افحص المؤشرات الظاهرة فعليًا.

لا تفترض وجود مؤشر غير ظاهر.

أعطني إشارة واحدة فقط:
CALL أو PUT.

ثم أعطني التحليل بالشكل المطلوب.
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            prompt,
            image
        ]
    )

    result = response.text.strip()

    trade_counter += 1

    trade = {
        "id": trade_counter,
        "time": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "analysis": result,
        "result": "PENDING"
    }

    trades.append(trade)

    await update.message.reply_text(
        f"🆔 الصفقة #{trade_counter}\n\n"
        f"{result}\n\n"
        "⏳ النتيجة: PENDING\n"
        "بعد انتهاء الصفقة أرسل WIN أو LOSS."
    )

except Exception as e:

    await update.message.reply_text(
        "❌ حدث خطأ أثناء التحليل:\n\n"
        f"{str(e)}"
    )

============================================================

WIN / LOSS

============================================================

async def result_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

if not is_owner(update):
    return

text = update.message.text.strip().upper()

if text not in ["WIN", "LOSS"]:
    return

if not trades:
    await update.message.reply_text(
        "⚠️ لا توجد صفقات مسجلة."
    )
    return

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
    f"{emoji} تم تسجيل الصفقة "
    f"#{pending_trade['id']} = {text}\n\n"
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
    trade
    for trade in trades
    if trade["result"] in ["WIN", "LOSS"]
]

wins = sum(
    1
    for trade in completed
    if trade["result"] == "WIN"
)

losses = sum(
    1
    for trade in completed
    if trade["result"] == "LOSS"
)

total = len(completed)

win_rate = (
    wins / total * 100
    if total > 0
    else 0
)

best_win_streak = 0
current_win_streak = 0

best_loss_streak = 0
current_loss_streak = 0

for trade in completed:

    if trade["result"] == "WIN":

        current_win_streak += 1
        current_loss_streak = 0

        best_win_streak = max(
            best_win_streak,
            current_win_streak
        )

    else:

        current_loss_streak += 1
        current_win_streak = 0

        best_loss_streak = max(
            best_loss_streak,
            current_loss_streak
        )

return {
    "total": total,
    "wins": wins,
    "losses": losses,
    "win_rate": win_rate,
    "best_win_streak": best_win_streak,
    "best_loss_streak": best_loss_streak
}

============================================================

STATS COMMAND

============================================================

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

MAIN

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
