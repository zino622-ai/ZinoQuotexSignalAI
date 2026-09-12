import os
import io
import json
import re
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from PIL import Image
from google import genai
from google.genai import types
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

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# HEALTH SERVER - RENDER
# =========================================================

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
            "10000"
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    server.serve_forever()


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(update: Update) -> bool:

    if not update.effective_user:
        return False

    return (
        update.effective_user.id
        == OWNER_ID
    )


# =========================================================
# START
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
        "📸 أرسل صورة واضحة للرسم البياني.\n\n"
        "سأقوم بتحليل:\n"
        "🏗️ Market Structure\n"
        "💧 Liquidity\n"
        "⚡ Momentum\n"
        "🕯️ Price Action\n"
        "📊 Support / Resistance\n"
        "✅ Confirmation Candle\n"
        "⚠️ False Breakout\n\n"
        "🎯 ثم أحدد اتجاه الحركة:\n"
        "🟢 CALL (UP)\n"
        "🔴 PUT (DOWN)\n\n"
        "📊 مع نسبة قوة الاتجاه ونسبة الثقة."
    )


# =========================================================
# HELP
# =========================================================

async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )

        return

    await update.message.reply_text(
        "📸 أرسل صورة واضحة للرسم البياني.\n\n"
        "يفضل أن يظهر:\n"
        "• اسم الأصل\n"
        "• الإطار الزمني\n"
        "• الشموع\n"
        "• المؤشرات إن وجدت\n"
        "• أكبر قدر ممكن من حركة السعر."
    )


# =========================================================
# ANALYSIS PROMPT
# =========================================================

ANALYSIS_PROMPT = r"""
أنت محلل تقني متخصص في قراءة الرسوم البيانية قصيرة المدى.

حلل الصورة المرفقة بدقة شديدة.

مهم جدًا:

لا تستخدم NO SIGNAL أبدًا.

يجب عليك دائمًا اختيار اتجاه واحد فقط:

UP
أو
DOWN

حتى عندما تكون الأدلة ضعيفة أو متضاربة، اختر الاتجاه الذي لديه ميل تحليلي أكبر، ولكن اخفض direction_percentage ليعكس ضعف الإشارة.

لا تعتبر direction_percentage احتمالًا إحصائيًا مضمونًا للفوز.
إنها فقط درجة قوة الأدلة الفنية لصالح الاتجاه المختار.

==================================================
1. MARKET STRUCTURE
==================================================

حلل:

- Higher Highs
- Higher Lows
- Lower Highs
- Lower Lows
- Break of Structure
- التحول في الاتجاه
- القمم والقيعان الأخيرة
- هل البنية صاعدة أم هابطة؟

أعط تقييمًا من 0 إلى 100 في:

structure_score


==================================================
2. LIQUIDITY
==================================================

حلل:

- مناطق السيولة
- أخذ القمم
- أخذ القيعان
- Liquidity Sweep
- Fake Breakout
- Rejection
- هل تم سحب السيولة قبل الحركة؟

أعط تقييمًا من 0 إلى 100 في:

liquidity_score


==================================================
3. MOMENTUM
==================================================

حلل:

- قوة الشموع
- سرعة الحركة
- حجم الأجسام
- الإغلاقات
- استمرار الحركة
- ضعف الزخم
- تسارع الحركة

أعط تقييمًا من 0 إلى 100 في:

momentum_score


==================================================
4. PRICE ACTION
==================================================

حلل:

- Bullish Reversal
- Bearish Reversal
- Engulfing
- Rejection Candle
- Strong Momentum Candle
- Continuation
- علامات الانعكاس

أعط تقييمًا من 0 إلى 100 في:

price_action_score


==================================================
5. SUPPORT / RESISTANCE
==================================================

حدد أهم مستويات الدعم والمقاومة الموجودة في الرسم.

استخدمها داخل التحليل والسبب وشمعة التأكيد.

لا تجعل Support وResistance أقسامًا منفصلة في النتيجة النهائية.


==================================================
6. CONFIRMATION CANDLE
==================================================

حدد هل توجد شمعة تأكيد حقيقية.

ركز على:

- الإغلاق فوق/تحت المستوى
- قوة جسم الشمعة
- الاتجاه
- استمرار الحركة
- رفض المستوى
- الاختراق الحقيقي أو الكاذب

أعط تقييمًا من 0 إلى 100 في:

confirmation_score


==================================================
7. FALSE BREAKOUT
==================================================

حدد:

true

إذا كان هناك اختراق كاذب واضح.

وإلا:

false


مهم:

وجود false_breakout لا يعني NO SIGNAL.

إذا كان الاتجاه واضحًا رغم ذلك، اختر UP أو DOWN وخفض direction_percentage.


==================================================
8. CONFLICT
==================================================

حدد:

true

إذا كانت الأدلة متضاربة بشكل واضح.

وإلا:

false


حتى إذا كان conflict = true:

لا تستخدم NO SIGNAL.

اختر الاتجاه الأقوى وخفض direction_percentage.


==================================================
9. FINAL DIRECTION
==================================================

يجب أن يكون:

UP

أو:

DOWN

ممنوع:

NEUTRAL

وممنوع:

NO SIGNAL


==================================================
10. DIRECTION PERCENTAGE
==================================================

direction_percentage يجب أن تكون بين:

50 و 99

التفسير:

50-59 = Weak
60-74 = Medium
75-89 = Strong
90-99 = Very Strong

هذه النسبة تمثل قوة الأدلة الفنية للاتجاه المختار فقط.

ليست ضمانًا للصفقة وليست احتمالًا إحصائيًا مؤكدًا.


==================================================
11. SHORT TERM TREND
==================================================

اكتب الاتجاه القصير بشكل واضح، مثل:

Strong Bullish Trend

Bullish Trend

Weak Bullish Trend

Strong Bearish Trend

Bearish Trend

Weak Bearish Trend


==================================================
12. REASON
==================================================

اكتب سببًا واضحًا ومختصرًا يشرح لماذا تم اختيار UP أو DOWN.

اذكر أهم العوامل:

- Market Structure
- Momentum
- Price Action
- Liquidity
- Confirmation
- أهم مستوى سعري عند الحاجة


==================================================
JSON FORMAT
==================================================

يجب أن يكون الرد JSON فقط.

بدون Markdown.

بدون ```json.

استخدم هذا الشكل بالضبط:

{
  "asset": "",
  "timeframe": "",

  "direction": "UP / DOWN",
  "direction_percentage": 0,

  "structure": "",
  "structure_score": 0,

  "liquidity": "",
  "liquidity_score": 0,

  "momentum": "",
  "momentum_score": 0,

  "price_action": "",
  "price_action_score": 0,

  "support": "",
  "resistance": "",

  "levels_score": 0,

  "confirmation": "",
  "confirmation_score": 0,

  "false_breakout": false,
  "conflict": false,

  "entry_price": "",
  "cancel_condition": "",

  "short_term_trend": "",

  "reason": ""
}
"""


# =========================================================
# JSON EXTRACTION
# =========================================================

def extract_json(text: str):

    text = text.strip()

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

    match = re.search(
        r"\{.*\}",
        text,
        flags=re.DOTALL
    )

    if not match:
        raise ValueError(
            "لم يتم العثور على JSON."
        )

    json_text = match.group(0)

    return json.loads(json_text)


# =========================================================
# SAFE NUMBER
# =========================================================

def number(value, default=0):

    try:
        return float(value)

    except (
        TypeError,
        ValueError
    ):

        return default


# =========================================================
# CALCULATE CONFIDENCE SCORE
# =========================================================

def calculate_score(data):

    structure = number(
        data.get("structure_score")
    )

    liquidity = number(
        data.get("liquidity_score")
    )

    momentum = number(
        data.get("momentum_score")
    )

    price_action = number(
        data.get("price_action_score")
    )

    levels = number(
        data.get("levels_score")
    )

    confirmation = number(
        data.get("confirmation_score")
    )

    total = (

        structure * 0.20

        + liquidity * 0.15

        + momentum * 0.15

        + price_action * 0.15

        + levels * 0.15

        + confirmation * 0.20
    )

    return round(
        total,
        1
    )


# =========================================================
# DETERMINE SIGNAL
# =========================================================

def determine_signal(data):

    direction = str(
        data.get(
            "direction",
            ""
        )
    ).upper().strip()

    if direction in (
        "UP",
        "CALL",
        "BULLISH"
    ):

        return "CALL"

    if direction in (
        "DOWN",
        "PUT",
        "BEARISH"
    ):

        return "PUT"

    # Fallback based on short-term trend
    trend = str(
        data.get(
            "short_term_trend",
            ""
        )
    ).lower()

    if any(
        word in trend
        for word in [
            "bearish",
            "down",
            "هابط",
            "هبوط"
        ]
    ):

        return "PUT"

    return "CALL"


# =========================================================
# DIRECTION PERCENTAGE
# =========================================================

def get_direction_percentage(data):

    percentage = number(
        data.get(
            "direction_percentage",
            50
        ),
        50
    )

    percentage = max(
        50.0,
        min(
            99.0,
            percentage
        )
    )

    return round(
        percentage,
        1
    )


# =========================================================
# SIGNAL STRENGTH
# =========================================================

def get_signal_strength(
    direction_percentage
):

    if direction_percentage >= 90:

        return "🔥 قوية جدًا"

    if direction_percentage >= 75:

        return "💪 قوية"

    if direction_percentage >= 60:

        return "⚠️ متوسطة"

    return "🟡 ضعيفة"


# =========================================================
# ANALYZE CHART
# =========================================================

async def analyze_chart(
    image_bytes
):

    image = Image.open(
        io.BytesIO(image_bytes)
    ).convert("RGB")

    response = await asyncio.to_thread(

        client.models.generate_content,

        model=GEMINI_MODEL,

        contents=[

            types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg"
            ),

            ANALYSIS_PROMPT
        ]
    )

    text = response.text

    data = extract_json(text)

    score = calculate_score(
        data
    )

    signal = determine_signal(
        data
    )

    direction_percentage = (
        get_direction_percentage(data)
    )

    data["final_score"] = score

    data["signal"] = signal

    data["direction_percentage"] = (
        direction_percentage
    )

    return data


# =========================================================
# FORMAT RESULT
# =========================================================

def format_result(data):

    signal = data.get(
        "signal",
        "CALL"
    )

    direction_percentage = (
        data.get(
            "direction_percentage",
            50
        )
    )

    confidence = data.get(
        "final_score",
        0
    )

    asset = data.get(
        "asset",
        "غير معروف"
    )

    timeframe = data.get(
        "timeframe",
        "غير معروف"
    )

    direction = data.get(
        "direction",
        "UP"
    )

    short_term_trend = data.get(
        "short_term_trend",
        ""
    )

    structure = data.get(
        "structure",
        ""
    )

    momentum = data.get(
        "momentum",
        ""
    )

    price_action = data.get(
        "price_action",
        ""
    )

    confirmation = data.get(
        "confirmation",
        ""
    )

    reason = data.get(
        "reason",
        ""
    )

    strength = get_signal_strength(
        direction_percentage
    )

    if signal == "CALL":

        signal_text = (
            f"🟢 CALL (UP) "
            f"{direction_percentage}%"
        )

    else:

        signal_text = (
            f"🔴 PUT (DOWN) "
            f"{direction_percentage}%"
        )

    result = (

        f"🎯 الإشارة: {signal_text}\n\n"

        f"📊 نسبة الثقة: "
        f"{confidence}%\n"

        f"📊 الأصل: "
        f"{asset}\n"

        f"📊 الإطار الزمني: "
        f"{timeframe}\n\n"

        f"🧭 الاتجاه: "
        f"{direction}\n"

        f"📈 الاتجاه القصير: "
        f"{short_term_trend}\n\n"

        f"🏗️ Market Structure:\n"
        f"{structure}\n\n"

        f"⚡ Momentum:\n"
        f"{momentum}\n\n"

        f"🕯️ Price Action:\n"
        f"{price_action}\n\n"

        f"✅ شمعة التأكيد:\n"
        f"{confirmation}\n\n"

        f"🧠 السبب:\n"
        f"{reason}\n\n"

        f"📌 قوة الإشارة: "
        f"{strength}\n\n"

        f"⚠️ هذه قراءة تحليلية وليست ضمانًا "
        f"لنتيجة الصفقة."
    )

    return result


# =========================================================
# PHOTO HANDLER
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
        "🔎 جاري تحليل الرسم البياني..."
    )

    try:

        photo_file = await (
            update.message.photo[-1]
            .get_file()
        )

        buffer = io.BytesIO()

        await photo_file.download_to_memory(
            buffer
        )

        image_bytes = buffer.getvalue()

        result = await analyze_chart(
            image_bytes
        )

        formatted_result = format_result(
            result
        )

        await status_message.edit_text(
            formatted_result
        )

    except Exception as e:

        print(
            "ANALYSIS ERROR:",
            repr(e)
        )

        await status_message.edit_text(
            "❌ حدث خطأ أثناء تحليل الصورة.\n\n"
            "حاول إرسال صورة أوضح."
        )


# =========================================================
# MAIN
# =========================================================

 def main():

    print(
        "🚀 Starting ZinoQuotexSignalAI..."
    )

    # Render health server
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
        MessageHandler(
            filters.PHOTO,
            photo
        )
    )

    print(
        "🧹 Cleaning old Telegram webhook..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":

    main()
