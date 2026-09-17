import os
import io
import asyncio
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


# ============================================================
# ZinoQuotexSignalAI
# Candle + Swing + Breakout Analysis
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Algeria time
ALGERIA_TZ = ZoneInfo("Africa/Algiers")

# Quotex chart timezone requested by user: UTC-3
QUOTEX_TZ = ZoneInfo("Etc/GMT+3")


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")


client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# ACCESS CONTROL
# ============================================================

def is_owner(user_id: int) -> bool:
    if not OWNER_ID:
        return True

    try:
        return str(user_id) == str(int(OWNER_ID))
    except Exception:
        return str(user_id) == str(OWNER_ID)


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
        "📸 أرسل Screenshot للشارت وسأحلله."
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
        "📸 أرسل صورة شارت Quotex.\n\n"
        "سيتم التركيز على:\n"
        "🕯️ افتتاح وإغلاق الشموع\n"
        "📉 القمم والقيعان\n"
        "💥 الانكسارات\n"
        "🔄 Fake Breakout\n"
        "🎯 اتجاه الصفقة\n"
        "⏱️ الدخول مع بداية الشمعة القادمة"
    )


# ============================================================
# TIME
# ============================================================

def get_times():
    now_algeria = datetime.now(ALGERIA_TZ)
    now_quotex = now_algeria.astimezone(QUOTEX_TZ)

    # بداية الشمعة القادمة على إطار M1
    next_minute = (now_quotex + timedelta(minutes=1)).replace(
        second=0,
        microsecond=0
    )

    return now_algeria, now_quotex, next_minute


# ============================================================
# GEMINI PROMPT
# ============================================================

ANALYSIS_PROMPT = """
أنت محلل Price Action متخصص في تحليل شارتات Quotex M1.

حلل الصورة فقط بناءً على ما يظهر فعليًا في الشارت.

الأولوية القصوى للتحليل تكون بهذا الترتيب:

1. إغلاق الشموع:
- قوة الإغلاق.
- مكان الإغلاق داخل جسم الشمعة.
- طول الفتيل العلوي والسفلي.
- هل الإغلاق يؤكد الحركة أو يرفضها.

2. افتتاح الشموع:
- سلوك افتتاح الشمعة التالية.
- هل الافتتاح يؤكد اتجاه الشمعة السابقة.
- هل يوجد رفض أو انعكاس عند الافتتاح.

3. القمم والقيعان:
- Swing High.
- Swing Low.
- Higher High.
- Higher Low.
- Lower High.
- Lower Low.
- راقب تسلسل القمم والقيعان وليس شمعة واحدة فقط.

4. الانكسارات:
- Breakout لقمة سابقة.
- Breakdown لقاع سابق.
- هل الكسر قوي ومؤكد بالإغلاق.
- هل الكسر مجرد Fake Breakout.
- راقب Liquidity Sweep فوق القمم أو تحت القيعان.

5. Price Action:
- قوة الدفع.
- رفض السعر.
- استمرار الحركة.
- انعكاس الحركة.
- شمعة التأكيد الأخيرة.

ممنوع الاعتماد الأساسي على RSI أو MACD أو أي مؤشر آخر.
الشموع + القمم والقيعان + الانكسارات هي الأساس.

مهم جدًا:
- لا تقل NO SIGNAL.
- يجب أن تختار اتجاهًا واحدًا فقط: CALL أو PUT.
- لا تعطِ اتجاهًا محايدًا.
- إذا كانت الإشارة ضعيفة، اختر الاتجاه الذي تدعمه البنية السعرية والإغلاق الأخير، مع خفض نسبة الثقة.
- لا تختر الاتجاه عشوائيًا.
- لا تختر CALL فقط لأن آخر شمعة خضراء.
- لا تختر PUT فقط لأن آخر شمعة حمراء.
- لا تعتمد على لون شمعة واحدة.
- افحص تسلسل القمم والقيعان والانكسارات أولًا.

الإطار الزمني:
M1.

الدخول:
يكون الدخول فقط في بداية الشمعة القادمة.

لا تعطِ وقت انتهاء.

أرجع النتيجة بهذا الشكل بالضبط:

🎯 الإشارة: 🟢 CALL (UP) XX%
📊 نسبة الثقة: XX%
📊 الأصل: [اسم الأصل الظاهر في الصورة]
📊 الإطار الزمني: 1M
⏱️ وقت الدخول: [سيتم وضعه من البوت]

🧭 الاتجاه: [Bullish أو Bearish]
📈 الاتجاه القصير: [Bullish أو Bearish]

Market Structure
[تحليل القمم والقيعان والانكسار باختصار]

Momentum
[تحليل قوة الحركة الحالية]

Price Action
[تحليل سلوك الشموع]

شمعة التأكيد
[اذكر آخر شمعة أو شمعة التأكيد ولماذا]

السبب
[سبب مختصر ومباشر لاختيار CALL أو PUT]

قواعد مهمة:
- لا تضف Support/Resistance.
- لا تضف مؤشرات.
- لا تضف أي قسم غير الأقسام المطلوبة.
- لا تذكر أنك نموذج ذكاء اصطناعي.
- لا تقل إن التحليل مضمون.
- لا تستخدم كلمة NO SIGNAL.
- الاتجاه يجب أن يكون CALL أو PUT فقط.
- نسبة الثقة بين 50% و95%.
- يجب أن تكون النسبة منطقية حسب قوة الأدلة الظاهرة في الشارت.
"""


# ============================================================
# IMAGE ANALYSIS
# ============================================================

async def analyze_chart(image_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        # تصغير الصورة إذا كانت كبيرة حتى تكون أخف على API
        max_size = 1600

        if image.width > max_size or image.height > max_size:
            image.thumbnail((max_size, max_size))

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=92)
        output.seek(0)

        image_part = types.Part.from_bytes(
            data=output.getvalue(),
            mime_type="image/jpeg"
        )

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=[
                ANALYSIS_PROMPT,
                image_part,
            ],
        )

        if not response or not response.text:
            raise RuntimeError("Gemini returned an empty response")

        return response.text.strip()

    except Exception as e:
        raise RuntimeError(str(e))


# ============================================================
# CLEAN GEMINI OUTPUT
# ============================================================

def clean_analysis(text: str, entry_time: str) -> str:
    text = text.strip()

    # منع Gemini من إضافة وقت مختلف
    lines = text.splitlines()
    cleaned = []

    for line in lines:
        lower = line.lower()

        if "وقت الدخول" in line:
            cleaned.append(f"⏱️ وقت الدخول: {entry_time}")
        else:
            cleaned.append(line)

    text = "\n".join(cleaned)

    # إذا Gemini لم يضع وقت الدخول
    if "⏱️ وقت الدخول:" not in text:
        text = text.rstrip() + f"\n⏱️ وقت الدخول: {entry_time}"

    return text


# ============================================================
# PHOTO HANDLER
# ============================================================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    if not is_owner(update.effective_user.id):
        return

    photo = update.message.photo

    if not photo:
        return

    processing_message = await update.message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:
        file = await photo[-1].get_file()
        image_bytes = await file.download_as_bytearray()

        _, _, next_candle = get_times()

        # وقت الدخول حسب توقيت Quotex UTC-3
        entry_time = next_candle.strftime("%H:%M")

        result = await analyze_chart(bytes(image_bytes))

        result = clean_analysis(
            result,
            entry_time
        )

        await processing_message.edit_text(result)

    except Exception as e:
        error_text = str(e)

        if len(error_text) > 1000:
            error_text = error_text[:1000]

        await processing_message.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{error_text}"
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("ERROR:", context.error)


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading


class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ZinoQuotexSignalAI is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", 10000))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"Health server running on port {port}")

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():
    print("==============================================")
    print("ZinoQuotexSignalAI")
    print("Candle + Swing + Breakout Analysis")
    print("==============================================")
    print(f"Gemini model: {GEMINI_MODEL}")
    print("Telegram bot starting...")

    # Render health server
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
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler
        )
    )

    application.add_error_handler(error_handler)

    print("Bot is running.")

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
