import os
import io
import json
import re
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
# Fast Smart Chart Analysis
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
)

# Fallback فقط عند 503 / 429 / مشاكل مؤقتة
FALLBACK_MODELS = [
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

ALGERIA_TZ = ZoneInfo("Africa/Algiers")

# توقيت Quotex المطلوب
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

def get_quotex_time():

    return datetime.now(
        ALGERIA_TZ
    ).astimezone(
        QUOTEX_TZ
    )


def calculate_entry_time(delay_minutes):

    now = get_quotex_time()

    # بداية الشمعة القادمة
    next_candle = (
        now + timedelta(minutes=1)
    ).replace(
        second=0,
        microsecond=0
    )

    entry = next_candle + timedelta(
        minutes=delay_minutes - 1
    )

    return entry.strftime("%H:%M")


# ============================================================
# FAST ANALYSIS PROMPT
# ============================================================

ANALYSIS_PROMPT = """
أنت محلل فني متخصص في تحليل صور شارت Quotex.

حلل الصورة فقط.
لا تخترع أي معلومة غير ظاهرة.

الهدف:
إعطاء CALL أو PUT مع تحديد أفضل وقت للدخول بناءً على حالة السوق الحالية.

لا تفترض أن الفريم M1.
اقرأ الفريم الظاهر في الشارت إذا كان واضحًا.

حلل بالترتيب:

1. Market Structure
- Higher High / Higher Low
- Lower High / Lower Low
- Trend أو Consolidation

2. Price Action
- قوة الشموع
- مكان الإغلاق
- الرفض
- الفتائل
- قوة الحركة

3. Support / Resistance
- استخدم القمم والقيعان الظاهرة.
- لا تدخل بمجرد لمس المستوى.
- انتظر تأكيد الشمعة.

4. Confirmation
راقب:
- Hammer / Pin Bar
- Bullish Engulfing
- Bearish Engulfing
- rejection candle
- strong close

5. Momentum
حدد هل الحركة الحالية قوية أم ضعيفة.

6. RSI
استخدمه فقط إذا كان ظاهرًا بوضوح.

7. EMA 5 / EMA 13
استخدمهما فقط إذا كانا ظاهرين بوضوح.

لا تخترع RSI أو EMA.

============================================================
قرار الصفقة
============================================================

اختر اتجاهًا واحدًا فقط:

CALL
أو
PUT

ممنوع:
NO SIGNAL
NEUTRAL

إذا كانت الأدلة ضعيفة، اختر الاتجاه الذي تدعمه الأدلة أكثر وخفض الثقة.

============================================================
وقت الدخول
============================================================

هذه أهم نقطة.

لا تجعل الدخول دائمًا بعد دقيقة.

حدد ENTRY_DELAY_MINUTES بناءً على التحليل الفعلي:

1 = فرصة جاهزة وقوية ويمكن الدخول قريبًا.

2 = الاتجاه واضح لكن يحتاج شمعة إضافية أو تأكيدًا بسيطًا.

3 = يحتاج تأكيدًا إضافيًا أو السعر يحتاج وقتًا للوصول لمنطقة الدخول.

4 أو 5 = إذا كان الاتجاه واضحًا لكن أفضلية الدخول تحتاج انتظارًا أطول.

لا تختار الرقم عشوائيًا.

إذا كان السعر عند دعم/مقاومة بدون تأكيد:
لا تعتبر اللحظة الحالية دخولًا.
اختر وقتًا يسمح بظهور التأكيد.

ENTRY_DELAY_MINUTES يجب أن يكون رقمًا صحيحًا من 1 إلى 5.

============================================================
الثقة
============================================================

الثقة بين 50 و95.

اربط الثقة بقوة الأدلة الفعلية.

لا تقل إن الصفقة مضمونة.

============================================================
إخراج JSON فقط
============================================================

أرجع JSON صالح فقط بهذا الشكل:

{
  "signal": "CALL",
  "confidence": 78,
  "asset": "EUR/USD",
  "timeframe": "1M",
  "direction": "Bullish",
  "short_direction": "Bullish",
  "entry_delay_minutes": 2,
  "market_structure": "شرح مختصر",
  "momentum": "شرح مختصر",
  "price_action": "شرح مختصر",
  "confirmation_candle": "نوع شمعة التأكيد",
  "reason": "سبب مباشر ومختصر"
}

القواعد:

- signal يجب أن يكون CALL أو PUT فقط.
- confidence رقم بين 50 و95.
- entry_delay_minutes رقم صحيح من 1 إلى 5.
- لا تستخدم NO SIGNAL.
- لا تستخدم NEUTRAL.
- لا تخترع اسم الأصل.
- لا تخترع الفريم.
- إذا الفريم غير واضح اكتب "غير واضح".
- لا تخترع RSI.
- لا تخترع EMA.
- لا تضف MACD.
- لا تضف Stochastic.
- لا تضف Fibonacci.
- لا تضف ZigZag.
- لا تضف مؤشرات أخرى.
- لا تضف وقت انتهاء.
- لا تكتب أي شيء خارج JSON.
"""


# ============================================================
# TEMPORARY ERROR DETECTION
# ============================================================

def is_temporary_error(error):

    text = str(error).upper()

    temporary = [
        "429",
        "500",
        "502",
        "503",
        "504",
        "UNAVAILABLE",
        "RESOURCE_EXHAUSTED",
        "OVERLOADED",
        "HIGH DEMAND",
    ]

    return any(
        item in text
        for item in temporary
    )


# ============================================================
# JSON CLEANER
# ============================================================

def parse_json_response(text):

    if not text:
        raise RuntimeError(
            "Gemini returned an empty response"
        )

    text = text.strip()

    # محاولة مباشرة
    try:
        return json.loads(text)
    except Exception:
        pass

    # إزالة markdown code fence
    text = re.sub(
        r"```json\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"```\s*",
        "",
        text
    )

    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    # استخراج أول JSON object
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:

        candidate = text[
            start:end + 1
        ]

        try:
            return json.loads(candidate)
        except Exception:
            pass

    raise RuntimeError(
        "Gemini returned invalid JSON"
    )


# ============================================================
# VALIDATE ANALYSIS
# ============================================================

def validate_analysis(data):

    if not isinstance(data, dict):
        raise RuntimeError(
            "Invalid analysis format"
        )

    signal = str(
        data.get("signal", "")
    ).upper().strip()

    if signal not in [
        "CALL",
        "PUT"
    ]:
        raise RuntimeError(
            "Invalid signal from Gemini"
        )

    # confidence
    try:
        confidence = int(
            data.get(
                "confidence",
                50
            )
        )
    except Exception:
        confidence = 50

    confidence = max(
        50,
        min(95, confidence)
    )

    # entry delay
    try:
        delay = int(
            data.get(
                "entry_delay_minutes"
            )
        )
    except Exception:
        raise RuntimeError(
            "Gemini did not provide a valid entry delay"
        )

    # مهم:
    # لا نرجع تلقائيًا إلى 1 دقيقة.
    if delay < 1 or delay > 5:
        raise RuntimeError(
            "Invalid entry delay"
        )

    data["signal"] = signal
    data["confidence"] = confidence
    data["entry_delay_minutes"] = delay

    data["asset"] = str(
        data.get(
            "asset",
            "غير واضح"
        )
    ).strip()

    data["timeframe"] = str(
        data.get(
            "timeframe",
            "غير واضح"
        )
    ).strip()

    data["direction"] = str(
        data.get(
            "direction",
            "غير واضح"
        )
    ).strip()

    data["short_direction"] = str(
        data.get(
            "short_direction",
            "غير واضح"
        )
    ).strip()

    data["market_structure"] = str(
        data.get(
            "market_structure",
            ""
        )
    ).strip()

    data["momentum"] = str(
        data.get(
            "momentum",
            ""
        )
    ).strip()

    data["price_action"] = str(
        data.get(
            "price_action",
            ""
        )
    ).strip()

    data["confirmation_candle"] = str(
        data.get(
            "confirmation_candle",
            ""
        )
    ).strip()

    data["reason"] = str(
        data.get(
            "reason",
            ""
        )
    ).strip()

    return data


# ============================================================
# GENERATE WITH MODEL
# ============================================================

def generate_analysis(
    model_name,
    image_part
):

    print(
        f"Trying Gemini: {model_name}"
    )

    config = types.GenerateContentConfig(
        temperature=0.2,
        response_mime_type="application/json",
        max_output_tokens=700,
    )

    response = client.models.generate_content(
        model=model_name,
        contents=[
            ANALYSIS_PROMPT,
            image_part
        ],
        config=config
    )

    if not response or not response.text:
        raise RuntimeError(
            "Empty Gemini response"
        )

    data = parse_json_response(
        response.text
    )

    data = validate_analysis(
        data
    )

    print(
        f"Gemini success: {model_name}"
    )

    return data


# ============================================================
# IMAGE ANALYSIS
# ============================================================

async def analyze_chart(
    image_bytes
):

    # لا نصغر الصورة.
    # نستخدم الصورة كما وصلت من Telegram.
    image = Image.open(
        io.BytesIO(image_bytes)
    ).convert("RGB")

    output = io.BytesIO()

    # JPEG بجودة عالية للحفاظ على تفاصيل الشارت.
    image.save(
        output,
        format="JPEG",
        quality=95
    )

    image_part = types.Part.from_bytes(
        data=output.getvalue(),
        mime_type="image/jpeg"
    )

    models = []

    if GEMINI_MODEL:
        models.append(
            GEMINI_MODEL
        )

    for model in FALLBACK_MODELS:

        if model not in models:
            models.append(model)

    last_error = None

    for model in models:

        try:

            return generate_analysis(
                model,
                image_part
            )

        except Exception as error:

            last_error = error

            print(
                f"Gemini error [{model}]: "
                f"{error}"
            )

            # لا ننتقل لنموذج آخر إلا
            # في الأخطاء المؤقتة.
            if is_temporary_error(
                error
            ):
                continue

            raise

    raise RuntimeError(
        "All Gemini models failed:\n"
        + str(last_error)
    )


# ============================================================
# FORMAT RESULT
# ============================================================

def format_result(data):

    signal = data["signal"]
    confidence = data["confidence"]

    if signal == "CALL":
        emoji = "🟢"
        direction_text = "CALL (UP)"
    else:
        emoji = "🔴"
        direction_text = "PUT (DOWN)"

    delay = data[
        "entry_delay_minutes"
    ]

    entry_time = calculate_entry_time(
        delay
    )

    result = (
        f"🎯 الإشارة: {emoji} "
        f"{direction_text} {confidence}%\n\n"

        f"📊 نسبة الثقة: {confidence}%\n"
        f"📊 الأصل: {data['asset']}\n"
        f"📊 الإطار الزمني: "
        f"{data['timeframe']}\n"
        f"⏱️ وقت الدخول: {entry_time}\n"
        f"⏳ الدخول بعد: {delay} دقيقة\n\n"

        f"🧭 الاتجاه: "
        f"{data['direction']}\n"
        f"📈 الاتجاه القصير: "
        f"{data['short_direction']}\n\n"

        f"Market Structure\n"
        f"{data['market_structure']}\n\n"

        f"Momentum\n"
        f"{data['momentum']}\n\n"

        f"Price Action\n"
        f"{data['price_action']}\n\n"

        f"شمعة التأكيد\n"
        f"{data['confirmation_candle']}\n\n"

        f"السبب\n"
        f"{data['reason']}"
    )

    return result


# ============================================================
# PHOTO HANDLER
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
        "🔎 تحليل الشارت..."
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

        analysis = await analyze_chart(
            bytes(image_bytes)
        )

        final_result = format_result(
            analysis
        )

        await processing.edit_text(
            final_result
        )

    except Exception as error:

        message = str(error)

        if len(message) > 1500:
            message = message[:1500]

        await processing.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            + message
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
        if total
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
        if total
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
        if total
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
# ERROR HANDLER
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
        "Fast Smart Chart Analysis"
    )
    print(
        "=========================================="
    )

    print(
        f"Primary Gemini: {GEMINI_MODEL}"
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
