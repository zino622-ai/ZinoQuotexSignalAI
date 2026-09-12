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


# ============================================================
# SETTINGS
# ============================================================

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


# ============================================================
# HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ZinoQuotexSignalAI is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    server.serve_forever()


# ============================================================
# ACCESS CONTROL
# ============================================================

def is_owner(update: Update) -> bool:
    if not update.effective_user:
        return False

    return update.effective_user.id == OWNER_ID


# ============================================================
# START
# ============================================================

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
        "📸 أرسل صورة واضحة للشارت.\n\n"
        "سيتم تحليل:\n"
        "• Market Structure\n"
        "• Liquidity\n"
        "• Momentum\n"
        "• Price Action\n"
        "• Support / Resistance\n"
        "• Confirmation Candle\n"
        "• False Breakout\n\n"
        "وفي النهاية:\n"
        "🟢 CALL\n"
        "🔴 PUT\n"
        "⚪ NO SIGNAL"
    )


# ============================================================
# HELP
# ============================================================

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
        "• Support / Resistance\n"
        "• أكبر قدر ممكن من حركة السعر السابقة."
    )


# ============================================================
# JSON EXTRACTION
# ============================================================

def extract_json(text: str):

    text = text.strip()

    # إزالة markdown code fences
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

    # البحث عن أول JSON object
    match = re.search(
        r"\{.*\}",
        text,
        flags=re.DOTALL
    )

    if not match:
        raise ValueError("لم يتم العثور على JSON.")

    json_text = match.group(0)

    return json.loads(json_text)


# ============================================================
# SAFE NUMBER
# ============================================================

def number(value, default=0):

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ============================================================
# SCORE ENGINE
# ============================================================

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

    return round(total, 1)


# ============================================================
# SIGNAL DECISION
# ============================================================

def determine_signal(data, score):

    direction = str(
        data.get("direction", "")
    ).upper()

    confirmation = str(
        data.get("confirmation", "")
    ).lower()

    false_breakout = bool(
        data.get("false_breakout", False)
    )

    conflict = bool(
        data.get("conflict", False)
    )

    # --------------------------------------------
    # NO SIGNAL CONDITIONS
    # --------------------------------------------

    if conflict:
        return "NO SIGNAL"

    if false_breakout:
        return "NO SIGNAL"

    if confirmation in (
        "",
        "none",
        "weak",
        "no"
    ):
        return "NO SIGNAL"

    if score < 70:
        return "NO SIGNAL"

    # --------------------------------------------
    # DIRECTION
    # --------------------------------------------

    if direction in ("UP", "CALL", "BULLISH"):
        return "CALL"

    if direction in ("DOWN", "PUT", "BEARISH"):
        return "PUT"

    return "NO SIGNAL"


# ============================================================
# ANALYSIS PROMPT
# ============================================================

ANALYSIS_PROMPT = r"""
أنت محلل تقني متخصص في Price Action وتحليل الشارتات.

مهمتك تحليل صورة الشارت فقط، بدون اختراع معلومات غير ظاهرة.

لا تعتمد على RSI أو MACD أو أي مؤشر غير ظاهر في الصورة.

ركز على:

1. MARKET STRUCTURE
- Higher High
- Higher Low
- Lower High
- Lower Low
- Break of Structure
- الاتجاه القصير المدى

2. LIQUIDITY
- مناطق السيولة
- Liquidity Sweep
- أخذ قمم أو قيعان سابقة
- Fake Breakout
- رفض الاختراق

3. MOMENTUM
- قوة الشموع
- سرعة الحركة
- قوة الإغلاق
- استمرار أو ضعف الزخم

4. PRICE ACTION
- شمعة انعكاسية
- Engulfing
- Rejection
- شمعة قوية
- شمعة تأكيد

5. SUPPORT / RESISTANCE
- أقرب Support واضح
- أقرب Resistance واضح
- هل السعر قريب جدًا من مستوى مهم؟
- هل حصل اختراق حقيقي أم مجرد اختراق لحظي؟

6. CONFIRMATION CANDLE
شمعة التأكيد مهمة جدًا.

لا تعتبر مجرد لمس Support أو Resistance إشارة.

لا تعتبر مجرد اختراق لحظي اختراقًا حقيقيًا.

إذا حدث اختراق لمقاومة ثم عاد السعر وأغلق تحتها،
فهذا قد يكون False Breakout.

إذا حدث اختراق Support ثم عاد السعر وأغلق فوقه،
فهذا قد يكون False Breakout.

إذا لم توجد شمعة تأكيد واضحة:
اجعل confirmation = "weak" أو "none".

7. TRADE DECISION
لا تجبر نفسك على CALL أو PUT.

إذا كانت الأدلة متناقضة أو غير كافية:
القرار يجب أن يكون NO SIGNAL.

أريد منك إعادة النتيجة بصيغة JSON فقط.

استخدم هذا الشكل بالضبط:

{
  "asset": "",
  "timeframe": "",

  "direction": "UP / DOWN / NEUTRAL",

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

قواعد الدرجات:

0 = لا يوجد دليل
20 = ضعيف جدًا
40 = ضعيف
60 = متوسط
70 = جيد
80 = قوي
90 = قوي جدًا
100 = واضح جدًا

لا ترفع الدرجة لمجرد وجود شمعة واحدة.

يجب أن يكون التحليل مبنيًا على السياق الكامل الظاهر في الشارت.

مهم جدًا:
إذا كانت الصورة غير واضحة أو لا يمكن قراءة الشموع والمستويات بشكل كافٍ،
استخدم درجات منخفضة واعتبر القرار النهائي NO SIGNAL.
"""


# ============================================================
# GEMINI ANALYSIS
# ============================================================

async def analyze_chart(image_bytes):

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

    score = calculate_score(data)

    signal = determine_signal(
        data,
        score
    )

    data["final_score"] = score
    data["signal"] = signal

    return data


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(data):

    signal = data.get(
        "signal",
        "NO SIGNAL"
    )

    score = data.get(
        "final_score",
        0
    )

    asset = data.get(
        "asset",
        "غير واضح"
    )

    timeframe = data.get(
        "timeframe",
        "غير واضح"
    )

    direction = data.get(
        "direction",
        "NEUTRAL"
    )

    entry = data.get(
        "entry_price",
        "غير محدد"
    )

    cancel = data.get(
        "cancel_condition",
        "غير محدد"
    )

    support = data.get(
        "support",
        "غير واضح"
    )

    resistance = data.get(
        "resistance",
        "غير واضح"
    )

    structure = data.get(
        "structure",
        "غير واضح"
    )

    liquidity = data.get(
        "liquidity",
        "غير واضح"
    )

    momentum = data.get(
        "momentum",
        "غير واضح"
    )

    price_action = data.get(
        "price_action",
        "غير واضح"
    )

    confirmation = data.get(
        "confirmation",
        "غير واضح"
    )

    trend = data.get(
        "short_term_trend",
        "غير واضح"
    )

    reason = data.get(
        "reason",
        "لا يوجد سبب واضح."
    )

    if signal == "CALL":
        signal_text = "🟢 CALL (UP)"

    elif signal == "PUT":
        signal_text = "🔴 PUT (DOWN)"

    else:
        signal_text = "⚪ NO SIGNAL"

    return (
        f"🎯 الإشارة: {signal_text}\n\n"

        f"📊 نسبة الثقة: {score}%\n"
        f"📊 الأصل: {asset}\n"
        f"📊 الإطار الزمني: {timeframe}\n\n"

        f"🧭 الاتجاه: {direction}\n"
        f"📈 الاتجاه القصير: {trend}\n\n"

        f"🧱 Support: {support}\n"
        f"🧱 Resistance: {resistance}\n\n"

        f"🏗️ Market Structure:\n"
        f"{structure}\n\n"

        f"💧 Liquidity:\n"
        f"{liquidity}\n\n"

        f"⚡ Momentum:\n"
        f"{momentum}\n\n"

        f"🕯️ Price Action:\n"
        f"{price_action}\n\n"

        f"✅ شمعة التأكيد:\n"
        f"{confirmation}\n\n"

        f"💰 سعر الدخول: {entry}\n"
        f"🚫 شرط الإلغاء: {cancel}\n\n"

        f"🧠 السبب:\n"
        f"{reason}\n\n"

        f"⚠️ هذا تحليل للشارت وليس ضمانًا لنتيجة الصفقة."
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

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
        "🔍 جاري تحليل الشارت...\n"
        "Structure + Liquidity + Momentum + Price Action"
    )

    try:

        telegram_file = await update.message.photo[-1].get_file()

        buffer = io.BytesIO()

        await telegram_file.download_to_memory(
            buffer
        )

        image_bytes = buffer.getvalue()

        result = await analyze_chart(
            image_bytes
        )

        message = format_signal(
            result
        )

        await status_message.edit_text(
            message
        )

    except Exception as e:

        print("ANALYSIS ERROR:", repr(e))

        await status_message.edit_text(
            "❌ حدث خطأ أثناء تحليل الصورة.\n\n"
            "تأكد أن الصورة واضحة وحاول مرة أخرى."
        )


# ============================================================
# TEXT HANDLER
# ============================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        await update.message.reply_text(
            "🔒 هذا البوت خاص وغير متاح للاستخدام."
        )
        return

    await update.message.reply_text(
        "📸 أرسل صورة للشارت حتى أقوم بتحليلها."
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "BOT ERROR:",
        repr(context.error)
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "🚀 Starting ZinoQuotexSignalAI..."
    )

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

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "✅ ZinoQuotexSignalAI is ready."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    health_thread = Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    main()
