import os
import json
import re
import logging
import threading
from datetime import datetime, timedelta, timezone
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
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

PORT = int(os.getenv("PORT", "10000"))

TIMEFRAME = "2M"

# Quotex timezone requested by user: UTC-3
USER_TIMEZONE = timezone(timedelta(hours=-3))


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

try:
    OWNER_ID = int(OWNER_ID)
except ValueError:
    raise RuntimeError("OWNER_ID must be an integer")


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("ZinoQuotexSignalAI")


# ============================================================
# GEMINI
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# SIMPLE STATS
# ============================================================

stats = {
    "total": 0,
    "wins": 0,
    "losses": 0,
}

last_signal = None


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

        self.wfile.write(
            b"ZinoQuotexSignalAI is running"
        )

    def log_message(self, format, *args):
        return


def start_health_server():
    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()

    logger.info("Health server running on port %s", PORT)


# ============================================================
# OWNER CHECK
# ============================================================

def is_owner(update: Update) -> bool:

    if not update.effective_user:
        return False

    return update.effective_user.id == OWNER_ID


# ============================================================
# TIME
# ============================================================

def now_user_time():
    return datetime.now(USER_TIMEZONE)


def next_candle_time():

    now = now_user_time()

    # 2-minute candle boundaries
    minute = now.minute

    next_even_minute = minute + (2 - minute % 2)

    if next_even_minute >= 60:

        target = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

    else:

        target = now.replace(
            minute=next_even_minute,
            second=0,
            microsecond=0
        )

    # Safety:
    # never allow entry time to be equal to or earlier than current time.
    if target <= now:
        target += timedelta(minutes=2)

    return target


# ============================================================
# GEMINI PROMPT
# ============================================================

SYSTEM_PROMPT = r"""
أنت Zino، محلل فني متخصص في تحليل صور شارت Quotex.

مهمتك تحليل صورة الشارت فقط وإعطاء إشارة اتجاهية واضحة:
UP = CALL
DOWN = PUT

الإطار الزمني المطلوب:
2M

لا تكتب NO SIGNAL.

لكن لا تعطِ ثقة مرتفعة لمجرد وجود شمعة واحدة.

============================================================
القواعد الأساسية للتحليل
============================================================

1) الاتجاه العام:

حدد الاتجاه العام من بنية السعر:

Bullish:
Higher Highs + Higher Lows

Bearish:
Lower Highs + Lower Lows

Sideways:
السعر يتحرك داخل نطاق بدون اتجاه واضح.

الاتجاه العام مهم جدًا.

============================================================

2) الاتجاه القصير:

حلل آخر عدة شموع فقط لمعرفة الزخم القصير.

لا تجعل الاتجاه القصير يلغي الاتجاه العام بسهولة.

إذا كان:
General = Bullish
Short = Bearish

فهذا غالبًا تصحيح، وليس تلقائيًا إشارة DOWN.

إذا كان:
General = Bearish
Short = Bullish

فهذا غالبًا ارتداد، وليس تلقائيًا إشارة UP.

============================================================

3) MARKET STRUCTURE

ركز على:

- Higher High
- Higher Low
- Lower High
- Lower Low
- Breakout
- Breakdown
- Retest
- Fake breakout
- Liquidity sweep
- rejection
- consolidation

لا تعتبر مجرد لمس مستوى أو Keltner سببًا كافيًا للدخول.

============================================================

4) CANDLE CONFIRMATION

افحص آخر شمعة مغلقة:

- Bullish candle
- Bearish candle
- rejection candle
- hammer
- pin bar
- engulfing
- strong body
- weak body
- close near high
- close near low

شمعة صغيرة وحدها ليست تأكيدًا قويًا.

============================================================

5) BREAKOUT

إذا حدث breakout:

لا تعتبره صالحًا مباشرة.

افحص:

- هل أغلقت الشمعة فوق المستوى؟
- هل الإغلاق قوي؟
- هل يوجد follow-through؟
- هل breakout حدث داخل consolidation؟
- هل هناك احتمال fake breakout؟

إذا كان breakout ضعيفًا، خفض الثقة.

============================================================

6) KELTNER

يمكن استخدام Keltner كعامل مساعد فقط.

لا تستخدم:
"لمس الحد العلوي = DOWN"

ولا:
"لمس الحد السفلي = UP"

يجب أن يتوافق Keltner مع:

Price Structure
Momentum
Candle Confirmation

============================================================

7) ADX / DI

ADX عامل مساعد.

ADX منخفض:
السوق ضعيف الاتجاه / قد يكون Sideways.

لا تعطِ 75% أو 80% ثقة عندما يكون ADX ضعيفًا جدًا إلا إذا كانت هناك بنية سعرية واضحة جدًا.

DI+ فوق DI- يدعم الصعود.

DI- فوق DI+ يدعم الهبوط.

لكن DI وحده ليس سببًا كافيًا للدخول.

============================================================

8) SIDEWAYS

إذا كان السوق Sideways:

لا تعتمد على شمعة صغيرة واحدة.

UP يحتاج:
- rejection واضح من قاع النطاق
أو
- breakout مؤكد للأعلى

DOWN يحتاج:
- rejection واضح من قمة النطاق
أو
- breakdown مؤكد للأسفل

إذا كان التأكيد ضعيفًا، لا ترفع confidence.

============================================================

9) تجنب مطاردة الحركة

إذا كانت آخر شمعة قوية جدًا وامتدت الحركة بالفعل:

لا تفترض أن الشمعة التالية ستكمل بنفس القوة.

ابحث عن:
- استمرار حقيقي
- retest
- confirmation

============================================================

10) توافق الاتجاه

الأولوية:

1. Market Structure
2. General Trend
3. Short Trend
4. Breakout / Breakdown
5. Candle Confirmation
6. Momentum
7. Keltner
8. ADX / DI

لا تجعل مؤشرًا واحدًا يتغلب على البنية السعرية.

============================================================
CONFIDENCE
============================================================

Confidence ليس رقمًا عشوائيًا.

تقريبًا:

55-60:
إشارة ضعيفة نسبيًا.

61-67:
تأكيد متوسط.

68-74:
عدة عوامل متوافقة.

75-82:
توافق قوي جدًا بين الاتجاه والبنية والزخم والتأكيد.

لا تستخدم 80% لمجرد أن شمعة واحدة قوية.

إذا كان الاتجاه العام Sideways وADX ضعيفًا:
لا ترفع confidence بسهولة.

============================================================
ENTRY TIME
============================================================

البوت يعمل على شارت 2M.

وقت الدخول يجب أن يكون وقتًا مستقبليًا.

لا تعطِ وقتًا مساويًا أو أقدم من وقت الشارت.

يفضل الدخول في بداية شمعة 2M مستقبلية.

============================================================
ENTRY PRICE
============================================================

استخرج سعر الدخول من الصورة إذا كان واضحًا.

إذا لم يكن السعر ظاهرًا بوضوح:
استخدم آخر سعر واضح في الشارت.

============================================================
CANCELLATION LEVEL
============================================================

قاعدة مهمة جدًا:

مستوى إلغاء الإشارة يجب أن يكون دائمًا أقل من سعر الدخول.

مثال:

سعر الدخول:
14.86839

الإلغاء:
14.86650

صيغة النص:

🛑 إلغاء إذا أغلقت شمعة تحت 14.86650

ممنوع أن يكون مستوى الإلغاء مساويًا لسعر الدخول.

ممنوع أن يكون أعلى من سعر الدخول.

حتى إذا كانت الإشارة DOWN، يجب أن يبقى مستوى الإلغاء أقل من سعر الدخول، لأن المستخدم يريد مقارنة المستويين مباشرة.

اختر مستوى إلغاء منطقيًا أسفل سعر الدخول، ويفضل أن يكون أسفل قاع/منطقة قريبة من البنية السعرية بدل رقم عشوائي.

============================================================
IMPORTANT
============================================================

لا تخترع بيانات غير ظاهرة في الصورة.

إذا لم تستطع قراءة الأصل، حاول استخراجه من الشارت.

لا تضف Support/Resistance section منفصلة.

ركز على:

Price Action
Market Structure
Breakout
Candle Confirmation
Momentum
Keltner
ADX

============================================================
OUTPUT
============================================================

أخرج JSON فقط.

بدون Markdown.

بدون ```.

بدون شرح خارج JSON.

الصيغة:

{
  "confidence": 72,
  "direction": "DOWN",
  "asset": "USD/ZAR",
  "timeframe": "2M",
  "general_trend": "Bearish",
  "short_trend": "Bearish",
  "structure": "Lower highs and lower lows with bearish continuation",
  "keltner": "Price rejected the upper area and moved lower",
  "adx": "Bearish DI alignment with improving trend strength",
  "confirmation_candle": "Strong bearish candle closing near its low",
  "reason": "Bearish structure, momentum and candle confirmation are aligned",
  "entry_price": 14.86969,
  "entry_delay_minutes": 2,
  "cancellation_price": 14.86650
}

شروط JSON:

confidence:
رقم صحيح بين 50 و85.

direction:
UP أو DOWN فقط.

entry_delay_minutes:
عدد صحيح بين 1 و3 فقط.

cancellation_price:
رقم أقل من entry_price دائمًا.

إذا كان cancellation_price >= entry_price:
يجب تصحيحه قبل إخراج JSON.

لا تضع وقت الدخول النصي داخل JSON.
البرنامج سيحسب وقت الدخول بنفسه.
"""


# ============================================================
# JSON CLEANING
# ============================================================

def extract_json(text: str):

    if not text:
        return None

    text = text.strip()

    # Remove markdown fences
    text = re.sub(
        r"^```(?:json)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"```$",
        "",
        text
    )

    text = text.strip()

    # Direct JSON
    try:
        return json.loads(text)
    except Exception:
        pass

    # Search JSON object
    match = re.search(
        r"\{.*\}",
        text,
        flags=re.DOTALL
    )

    if match:

        candidate = match.group(0)

        try:
            return json.loads(candidate)
        except Exception:
            return None

    return None


# ============================================================
# GEMINI ANALYSIS
# ============================================================

def analyze_image(image: Image.Image):

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(
                data=image_to_bytes(image),
                mime_type="image/png"
            ),
            SYSTEM_PROMPT,
        ],
        config=types.GenerateContentConfig(
            temperature=0.15,
            max_output_tokens=1200,
            response_mime_type="application/json",
        ),
    )

    text = getattr(response, "text", None)

    if not text:
        raise RuntimeError(
            "Gemini returned an empty response"
        )

    data = extract_json(text)

    if not data:
        raise RuntimeError(
            "Gemini returned invalid JSON"
        )

    return data


def image_to_bytes(image: Image.Image):

    import io

    buffer = io.BytesIO()

    # Do NOT resize the image.
    # Keep original resolution/quality.
    image.save(
        buffer,
        format="PNG"
    )

    return buffer.getvalue()


# ============================================================
# VALIDATE ANALYSIS
# ============================================================

def validate_analysis(data):

    direction = str(
        data.get("direction", "")
    ).upper().strip()

    if direction not in ("UP", "DOWN"):
        raise RuntimeError(
            "Gemini returned invalid direction"
        )

    try:
        confidence = int(
            data.get("confidence", 0)
        )
    except Exception:
        confidence = 0

    confidence = max(
        50,
        min(85, confidence)
    )

    try:
        entry_price = float(
            data.get("entry_price")
        )
    except Exception:
        raise RuntimeError(
            "Gemini did not return a valid entry price"
        )

    try:
        cancellation_price = float(
            data.get("cancellation_price")
        )
    except Exception:
        raise RuntimeError(
            "Gemini did not return a valid cancellation price"
        )

    # REQUIRED BY USER:
    # cancellation must always be BELOW entry price.
    if cancellation_price >= entry_price:

        # Create a small logical distance
        # while remaining below entry.
        distance = max(
            abs(entry_price) * 0.00015,
            0.00001
        )

        cancellation_price = (
            entry_price - distance
        )

    try:
        delay = int(
            data.get("entry_delay_minutes", 1)
        )
    except Exception:
        delay = 1

    delay = max(
        1,
        min(3, delay)
    )

    data["direction"] = direction
    data["confidence"] = confidence
    data["entry_price"] = entry_price
    data["cancellation_price"] = cancellation_price
    data["entry_delay_minutes"] = delay

    return data


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(data):

    direction = data["direction"]

    if direction == "UP":
        emoji = "🟢"
        label = "UP — شراء (Call)"
    else:
        emoji = "🔴"
        label = "DOWN — بيع (Put)"

    confidence = data["confidence"]

    asset = data.get(
        "asset",
        "Unknown"
    )

    timeframe = data.get(
        "timeframe",
        TIMEFRAME
    )

    general_trend = data.get(
        "general_trend",
        "Unknown"
    )

    short_trend = data.get(
        "short_trend",
        "Unknown"
    )

    structure = data.get(
        "structure",
        "Price structure analyzed"
    )

    keltner = data.get(
        "keltner",
        "Keltner used as supporting factor"
    )

    adx = data.get(
        "adx",
        "ADX used as supporting factor"
    )

    candle = data.get(
        "confirmation_candle",
        "Candle confirmation analyzed"
    )

    reason = data.get(
        "reason",
        "Multiple price-action factors are aligned"
    )

    entry_price = data["entry_price"]

    cancellation_price = data[
        "cancellation_price"
    ]

    delay = data[
        "entry_delay_minutes"
    ]

    # Calculate future entry time
    entry_time = (
        now_user_time()
        + timedelta(minutes=delay)
    )

    # Force seconds to zero for clean display
    entry_time = entry_time.replace(
        second=0,
        microsecond=0
    )

    # Make sure future
    if entry_time <= now_user_time():
        entry_time += timedelta(
            minutes=2
        )

    entry_time_text = (
        entry_time.strftime("%H:%M:%S")
        + " UTC-3"
    )

    chart_time = (
        now_user_time()
        .strftime("%H:%M:%S")
        + " UTC-3"
    )

    message = f"""
🎓 تحليل زينو

🎯 Confidence: {confidence}%
📊 {asset} · ⏱ {timeframe}
━━━━━━━━━━━━━━

🎯 القرار: {emoji} {label}
🕐 وقت الشارت: {chart_time}
⏰ وقت الدخول: {entry_time_text}
💵 سعر الدخول: {entry_price:.8f}
━━━━━━━━━━━━━━

📐 الاتجاه: {general_trend}
📈 الاتجاه القصير: {short_trend}
🏗 البنية السعرية: {structure}
〽️ Keltner 20/10: {keltner}
📊 ADX 14/14: {adx}
🕯 شمعة التأكيد: {candle}
━━━━━━━━━━━━━━

🧠 شرح زينو: {reason}

🛑 إلغاء إذا أغلقت شمعة تحت {cancellation_price:.8f}

━━━━━━━━━━━━━━
⚠️ التحليل مبني على الشارت المرسل فقط.
"""

    return message.strip()


# ============================================================
# RESULT BUTTONS
# ============================================================

def result_keyboard():

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ WIN",
                callback_data="trade_win"
            ),
            InlineKeyboardButton(
                "❌ LOSS",
                callback_data="trade_loss"
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 الإحصائيات",
                callback_data="trade_stats"
            ),
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# STATS TEXT
# ============================================================

def stats_text():

    total = stats["total"]
    wins = stats["wins"]
    losses = stats["losses"]

    if total > 0:
        winrate = (
            wins / total
        ) * 100
    else:
        winrate = 0

    return (
        "📊 إحصائيات زينو\n"
        "━━━━━━━━━━━━━━\n"
        f"🎯 الصفقات: {total}\n"
        f"✅ WIN: {wins}\n"
        f"❌ LOSS: {losses}\n"
        f"📈 Win Rate: {winrate:.1f}%"
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    await update.message.reply_text(
        "🎓 ZinoQuotexSignalAI\n\n"
        "📸 أرسل صورة الشارت لتحليلها.\n\n"
        "⏱ الإطار: 2M\n"
        "🌍 التوقيت: UTC-3\n\n"
        "استخدم /stats لعرض الإحصائيات."
    )


# ============================================================
# /STATS
# ============================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    await update.message.reply_text(
        stats_text()
    )


# ============================================================
# /RESET
# ============================================================

async def reset_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    stats["total"] = 0
    stats["wins"] = 0
    stats["losses"] = 0

    await update.message.reply_text(
        "♻️ تم تصفير إحصائيات الصفقات."
    )


# ============================================================
# IMAGE HANDLER
# ============================================================

async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global last_signal

    if not is_owner(update):
        return

    if not update.message or not update.message.photo:
        return

    status_message = await update.message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:

        photo = update.message.photo[-1]

        file = await context.bot.get_file(
            photo.file_id
        )

        import io

        image_bytes = await file.download_as_bytearray()

        image = Image.open(
            io.BytesIO(image_bytes)
        )

        # Keep original quality/resolution
        image.load()

        # Convert safely
        if image.mode not in (
            "RGB",
            "RGBA"
        ):
            image = image.convert("RGB")

        data = analyze_image(image)

        data = validate_analysis(data)

        # Save latest signal
        last_signal = data

        signal_message = format_signal(
            data
        )

        await status_message.edit_text(
            signal_message,
            reply_markup=result_keyboard()
        )

    except Exception as e:

        logger.exception(
            "Analysis error"
        )

        error_text = str(e)

        # Keep Telegram message readable
        if len(error_text) > 800:
            error_text = error_text[:800]

        await status_message.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            + error_text
        )


# ============================================================
# CALLBACK BUTTONS
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.callback_query:
        return

    query = update.callback_query

    await query.answer()

    if not update.effective_user:
        return

    if update.effective_user.id != OWNER_ID:
        return

    action = query.data

    if action == "trade_win":

        stats["total"] += 1
        stats["wins"] += 1

        await query.edit_message_reply_markup(
            reply_markup=None
        )

        await query.message.reply_text(
            "✅ تم تسجيل WIN\n\n"
            + stats_text()
        )

    elif action == "trade_loss":

        stats["total"] += 1
        stats["losses"] += 1

        await query.edit_message_reply_markup(
            reply_markup=None
        )

        await query.message.reply_text(
            "❌ تم تسجيل LOSS\n\n"
            + stats_text()
        )

    elif action == "trade_stats":

        await query.message.reply_text(
            stats_text()
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    error = context.error

    logger.error(
        "Telegram error: %s",
        error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    start_health_server()

    logger.info(
        "ZinoQuotexSignalAI started."
    )

    logger.info(
        "Gemini model: %s",
        GEMINI_MODEL
    )

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats_command
        )
    )

    application.add_handler(
        CommandHandler(
            "reset",
            reset_command
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
