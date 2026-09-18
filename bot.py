import os
import io
import json
import asyncio
import threading
from datetime import timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from google.genai import types
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_TEXT = os.getenv("OWNER_ID")

# الموديل الحالي الذي يعمل عندك
GEMINI_MODEL = "gemini-3.5-flash-lite"

# توقيت Quotex
UTC_MINUS_3 = timezone(timedelta(hours=-3))


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID_TEXT:
    raise RuntimeError("OWNER_ID is missing")

try:
    OWNER_ID = int(OWNER_ID_TEXT)
except ValueError:
    raise RuntimeError("OWNER_ID must be a number")


# ============================================================
# GEMINI CLIENT
# ============================================================

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(
        timeout=15000
    )
)


# ============================================================
# STATS
# ============================================================

wins = 0
losses = 0


# ============================================================
# ANALYSIS PROMPT
# ============================================================

PROMPT = """
أنت ZINO SIGNAL AI.

مهمتك تحليل صورة شارت Quotex المرسلة فقط، ثم اختيار اتجاه واحد:

UP أو DOWN

أنت محلل صارم.
لا تحاول إعطاء UP دائمًا.
لا تحاول إعطاء DOWN دائمًا.
كل صورة يتم تحليلها بشكل مستقل.

ممنوع اختراع أي معلومة غير ظاهرة في الصورة.

============================================================
أولًا: PRICE ACTION
============================================================

Price Action هو العنصر الأساسي في التحليل.

افحص آخر 8 إلى 15 شمعة ظاهرة بوضوح قدر الإمكان.

حلل:

- Open
- Close
- High
- Low
- جسم الشمعة
- الظلال / Wicks
- قوة الشموع
- القمم والقيعان
- Higher High
- Higher Low
- Lower High
- Lower Low
- Breakout
- Fake Breakout
- Rejection
- Consolidation
- آخر شمعة مغلقة

أعطِ أهمية أكبر للشموع الأخيرة.

إذا كان السعر يصنع:

Higher High + Higher Low
فهذا دليل صاعد.

إذا كان السعر يصنع:

Lower High + Lower Low
فهذا دليل هابط.

لكن لا تعتمد على الاتجاه العام وحده.
يجب فحص آخر حركة للسعر أيضًا.

============================================================
BREAKOUT
============================================================

لا تعتبر الـ Wick وحده اختراقًا.

اختراق حقيقي يحتاج إغلاق شمعة واضح خارج المستوى أو القمة/القاع المهمة.

إذا تجاوز السعر المستوى بالـ Wick فقط ثم عاد وأغلق داخله:

اعتبر ذلك رفضًا أو Fake Breakout.

لا تبني UP أو DOWN قوي على Wick فقط.

الإغلاق أهم من مجرد اللمس.

============================================================
آخر شمعة مغلقة
============================================================

ركز على آخر شمعة مكتملة.

افحص:

- هل أغلقت صاعدة أم هابطة؟
- هل جسمها قوي أم ضعيف؟
- أين أغلقت بالنسبة إلى نطاقها؟
- هل لها Wick طويل؟
- هل تؤكد الاتجاه؟
- هل تعارض الاتجاه؟
- هل جاءت بعد Breakout؟
- هل جاءت بعد Rejection؟

لا تعتبر الشمعة الحالية التي لم تغلق تأكيدًا كاملًا.

============================================================
ثانيًا: KELTNER CHANNEL
============================================================

استخدم Keltner Channel الظاهر في الصورة فقط.

الإعداد:

Keltner Channel 20 / 10

افحص:

- اتجاه القناة.
- ميل القناة.
- مكان السعر داخل القناة.
- موقع السعر بالنسبة للخط الأوسط.
- هل السعر يتحرك مع اتجاه القناة؟
- هل يوجد Breakout واضح؟
- هل يوجد Rejection؟

مهم:

مجرد لمس الحد العلوي لا يعني تلقائيًا DOWN.

مجرد لمس الحد السفلي لا يعني تلقائيًا UP.

Keltner لا يستطيع وحده تحديد القرار.

يجب دمجه مع Price Action و ADX.

لا تخترع قيم Keltner غير الظاهرة.

============================================================
ثالثًا: ADX
============================================================

استخدم ADX الظاهر في الصورة فقط.

الإعداد:

ADX 14 / 14

افحص:

- قوة الاتجاه.
- ADX.
- +DI.
- -DI.

إذا كان +DI يدعم الحركة الصاعدة:
هذا عامل لصالح UP.

إذا كان -DI يدعم الحركة الهابطة:
هذا عامل لصالح DOWN.

ADX وحده لا يحدد الاتجاه.

إذا كان الاتجاه ضعيفًا أو السوق يتحرك جانبيًا:
اخفض الثقة.

لا تخترع رقم ADX أو DI إذا لم يكن واضحًا في الصورة.

============================================================
ترتيب الأدلة
============================================================

رتب الأدلة بهذا الترتيب:

1. Price Action / Market Structure
2. آخر شمعة مغلقة
3. Breakout أو Rejection
4. Keltner Channel
5. ADX + DI

Price Action هو الأساس.

============================================================
قرار UP
============================================================

اختر UP عندما تكون الأدلة الصاعدة هي الأقوى.

أمثلة على الأدلة:

- Higher High / Higher Low.
- آخر شمعة مغلقة صاعدة.
- إغلاق قوي في الاتجاه الصاعد.
- Breakout صاعد مؤكد بالإغلاق.
- Rejection هابط ثم إغلاق صاعد.
- Keltner يدعم الاتجاه الصاعد.
- +DI يدعم الحركة عندما يكون ظاهرًا.

لا تعطِ UP بثقة عالية إذا كانت البنية السعرية هابطة بوضوح.

============================================================
قرار DOWN
============================================================

اختر DOWN عندما تكون الأدلة الهابطة هي الأقوى.

أمثلة على الأدلة:

- Lower High / Lower Low.
- آخر شمعة مغلقة هابطة.
- إغلاق قوي في الاتجاه الهابط.
- Breakout هابط مؤكد بالإغلاق.
- Rejection صاعد ثم إغلاق هابط.
- Keltner يدعم الاتجاه الهابط.
- -DI يدعم الحركة عندما يكون ظاهرًا.

لا تعطِ DOWN بثقة عالية إذا كانت البنية السعرية صاعدة بوضوح.

============================================================
CONSOLIDATION / RANGE
============================================================

إذا كان السوق يتحرك أفقيًا:

- لا تعتبر كل شمعة اتجاهًا.
- لا تعتبر كل Wick اختراقًا.
- لا تعتبر لمس Keltner انعكاسًا تلقائيًا.
- ابحث عن إغلاق مؤكد خارج النطاق.
- إذا لم يوجد Breakout مؤكد، اعتبر السوق ضعيف الاتجاه.

إذا كانت الأدلة مختلطة:
اختر الاتجاه الذي لديه أقوى دليل واضح،
لكن اخفض Confidence.

لا تصنع ثقة عالية من سوق جانبي.

============================================================
CONFIDENCE
============================================================

Confidence يجب أن تعكس قوة الأدلة الحقيقية.

50-59%:
أدلة ضعيفة أو سوق جانبي أو تعارض واضح.

60-69%:
اتجاه موجود لكن التأكيد متوسط.

70-79%:
عدة عوامل متفقة بقوة.

80-85%:
فقط عندما تكون:

Price Action
+
آخر شمعة
+
Breakout/Rejection
+
Keltner
+
ADX/DI

متفقة بشكل قوي ولا يوجد تعارض مهم.

لا تستخدم أكثر من 85%.

لا تستخدم 80% فقط لأن عدة شموع خضراء أو حمراء ظهرت.

لا ترفع الثقة لمجرد أن Keltner وADX يؤيدان الاتجاه إذا كان Price Action يعارضه.

============================================================
قاعدة التعارض
============================================================

إذا كانت الأدلة متعارضة:

لا تتجاهل التعارض.

مثال:

Price Action = DOWN
لكن Keltner = UP

هنا لا تعطي Confidence عالية.

مثال آخر:

Price Action = UP
لكن آخر شمعة مغلقة هابطة بقوة وتحت مستوى مهم.

هنا يجب تخفيض Confidence.

القرار النهائي يجب أن يعتمد على أقوى مجموعة أدلة، وليس على مؤشر واحد.

============================================================
التوازن بين UP و DOWN
============================================================

لا يوجد أي تفضيل مسبق لـ UP.

لا يوجد أي تفضيل مسبق لـ DOWN.

لا تحاول تعويض الإشارة السابقة.

إذا كانت الإشارة السابقة UP:
هذا لا يعني أن الحالية يجب أن تكون DOWN.

إذا كانت الإشارة السابقة DOWN:
هذا لا يعني أن الحالية يجب أن تكون UP.

حلل الصورة الحالية فقط.

============================================================
TIMEFRAME
============================================================

المستخدم يعمل على Quotex بإطار:

2M

إذا كان الإطار ظاهرًا في الصورة:
اقرأه.

إذا كان غير ظاهر:
استخدم 2M لأن المستخدم يعمل عليه.

لا تخترع إطارًا آخر.

============================================================
ENTRY TIME
============================================================

توقيت المستخدم:

UTC-3

الدخول يكون في بداية الشمعة القادمة.

اقرأ وقت الشارت إذا كان ظاهرًا.

بما أن الإطار 2M:
حدد بداية الشمعة القادمة بناءً على وقت الشارت.

أعطِ وقت الدخول فقط.

ممنوع إعطاء وقت انتهاء.

ممنوع إعطاء Expiry.

ممنوع قول:
"بعد ساعة".

ممنوع قول:
"بعد عدة دقائق" بدون وقت محدد.

إذا كان وقت الشارت غير واضح:
entry_time = "غير واضح"

============================================================
ENTRY PRICE
============================================================

إذا كان سعر الدخول يمكن تحديده من الصورة:
ضعه.

إذا لم يكن واضحًا:
entry_price = "غير واضح"

لا تخترع سعرًا.

============================================================
ASSET
============================================================

اقرأ اسم الأصل من الصورة.

إذا كان واضحًا:
استخدمه.

إذا لم يكن واضحًا:
asset = "غير واضح"

لا تخترع اسم الأصل.

============================================================
CANCELLATION
============================================================

شرط الإلغاء يجب أن يعتمد على مستوى واضح في الصورة.

مثال:

"تُلغى الإشارة إذا أغلقت شمعة قبل الدخول عكس مستوى الاختراق."

إذا لم يوجد مستوى واضح:
cancellation = "غير واضح"

لا تخترع رقمًا أو مستوى.

============================================================
ممنوع استخدام أي مؤشر إضافي
============================================================

استخدم فقط:

1. Price Action
2. Keltner Channel 20/10
3. ADX 14/14

ممنوع استخدام:

RSI
MACD
EMA
SMA
Moving Average
Parabolic SAR
Stochastic
Bollinger Bands
أو أي مؤشر آخر.

============================================================
FINAL DECISION
============================================================

يجب إعطاء قرار واحد فقط:

UP

أو

DOWN

لا تستخدم:

CALL
PUT
NO SIGNAL

حتى عندما تكون الثقة ضعيفة، اختر الاتجاه الذي تدعمه الأدلة الأقوى وخفض Confidence بدل اختراع ثقة عالية.

============================================================
OUTPUT
============================================================

أرجع JSON صالح فقط.

بدون Markdown.
بدون ```json.
بدون أي كلام خارج JSON.

استخدم هذه الحقول فقط:

{
  "decision": "UP أو DOWN",
  "confidence": 0,
  "asset": "اسم الأصل",
  "timeframe": "2M",
  "chart_time": "وقت الشارت",
  "entry_time": "وقت بداية الشمعة القادمة UTC-3",
  "entry_price": "سعر الدخول أو غير واضح",
  "trend": "Bullish أو Bearish أو Sideways",
  "short_trend": "Bullish أو Bearish أو Sideways",
  "structure": "وصف مختصر للقمم والقيعان والاختراق",
  "keltner": "وصف مختصر لحالة Keltner",
  "adx": "وصف مختصر لـ ADX وDI",
  "candle": "وصف مختصر لآخر شمعة مغلقة",
  "reason": "السبب الرئيسي للقرار",
  "cancellation": "شرط إلغاء الإشارة"
}

الدقة أهم من الكلام.

حلل الصورة أولًا.
حدد Market Structure.
ثم افحص آخر شمعة مغلقة.
ثم Breakout أو Rejection.
ثم Keltner.
ثم ADX وDI.
ثم قارن الأدلة.
ثم اختر UP أو DOWN.
ثم حدد Confidence حسب قوة التأكيد الحقيقية.
"""


# ============================================================
# JSON SCHEMA
# ============================================================

SCHEMA = {
    "type": "OBJECT",
    "properties": {

        "decision": {
            "type": "STRING",
            "enum": ["UP", "DOWN"]
        },

        "confidence": {
            "type": "INTEGER"
        },

        "asset": {
            "type": "STRING"
        },

        "timeframe": {
            "type": "STRING"
        },

        "chart_time": {
            "type": "STRING"
        },

        "entry_time": {
            "type": "STRING"
        },

        "entry_price": {
            "type": "STRING"
        },

        "trend": {
            "type": "STRING"
        },

        "short_trend": {
            "type": "STRING"
        },

        "structure": {
            "type": "STRING"
        },

        "keltner": {
            "type": "STRING"
        },

        "adx": {
            "type": "STRING"
        },

        "candle": {
            "type": "STRING"
        },

        "reason": {
            "type": "STRING"
        },

        "cancellation": {
            "type": "STRING"
        }
    },

    "required": [
        "decision",
        "confidence",
        "asset",
        "timeframe",
        "chart_time",
        "entry_time",
        "entry_price",
        "trend",
        "short_trend",
        "structure",
        "keltner",
        "adx",
        "candle",
        "reason",
        "cancellation"
    ]
}


# ============================================================
# HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"ZinoQuotexSignalAI is running"
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    server.serve_forever()


threading.Thread(
    target=start_health_server,
    daemon=True
).start()


# ============================================================
# OWNER
# ============================================================

def is_owner(update: Update):

    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


# ============================================================
# GEMINI REQUEST
# ============================================================

def gemini_request(image_bytes):

    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type="image/jpeg"
    )

    response = client.models.generate_content(

        model=GEMINI_MODEL,

        contents=[
            PROMPT,
            image_part
        ],

        config=types.GenerateContentConfig(

            response_mime_type="application/json",

            response_schema=SCHEMA,

            temperature=0.0,

            max_output_tokens=500
        )
    )

    return response.text


# ============================================================
# ANALYZE
# ============================================================

async def analyze_image(image_bytes):

    try:

        result = await asyncio.wait_for(

            asyncio.to_thread(
                gemini_request,
                image_bytes
            ),

            timeout=14
        )

        if not result:

            raise ValueError(
                "Gemini returned an empty response"
            )

        result = result.strip()

        if result.startswith("```"):

            result = result.replace(
                "```json",
                ""
            )

            result = result.replace(
                "```",
                ""
            )

            result = result.strip()

        try:

            data = json.loads(result)

        except json.JSONDecodeError:

            raise ValueError(
                "Gemini returned invalid JSON"
            )

        # ====================================================
        # VALIDATE DECISION
        # ====================================================

        decision = str(
            data.get(
                "decision",
                ""
            )
        ).upper()

        if decision not in ["UP", "DOWN"]:

            raise ValueError(
                "Invalid decision returned by Gemini"
            )

        data["decision"] = decision

        # ====================================================
        # LIMIT CONFIDENCE
        # ====================================================

        try:

            confidence = int(
                data.get(
                    "confidence",
                    0
                )
            )

        except (ValueError, TypeError):

            confidence = 0

        confidence = max(
            50,
            min(
                confidence,
                85
            )
        )

        data["confidence"] = confidence

        # ====================================================
        # FORCE 2M
        # ====================================================

        timeframe = str(
            data.get(
                "timeframe",
                ""
            )
        ).upper()

        if not timeframe:

            data["timeframe"] = "2M"

        # ====================================================
        # DEFAULT VALUES
        # ====================================================

        fields = [
            "asset",
            "chart_time",
            "entry_time",
            "entry_price",
            "trend",
            "short_trend",
            "structure",
            "keltner",
            "adx",
            "candle",
            "reason",
            "cancellation"
        ]

        for field in fields:

            if not data.get(field):

                data[field] = "غير واضح"

        return data

    except asyncio.TimeoutError:

        raise ValueError(
            "Gemini لم يكمل التحليل خلال 14 ثانية."
        )

    except Exception as e:

        error = str(e)

        if "504" in error:

            raise ValueError(
                "Gemini 504: انتهت مهلة التحليل."
            )

        if "503" in error:

            raise ValueError(
                "Gemini 503: الخدمة مشغولة حاليًا."
            )

        if "429" in error:

            raise ValueError(
                "Gemini 429: تم تجاوز حد الطلبات."
            )

        if "404" in error:

            raise ValueError(
                "Gemini 404: تحقق من توفر النموذج."
            )

        raise ValueError(error)


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(data):

    decision = str(
        data.get(
            "decision",
            "UP"
        )
    ).upper()

    if decision == "DOWN":

        direction = "🔴 DOWN — بيع (Put)"

    else:

        direction = "🟢 UP — شراء (Call)"

    confidence = data.get(
        "confidence",
        0
    )

    asset = data.get(
        "asset",
        "غير واضح"
    )

    timeframe = data.get(
        "timeframe",
        "2M"
    )

    chart_time = data.get(
        "chart_time",
        "غير واضح"
    )

    entry_time = data.get(
        "entry_time",
        "غير واضح"
    )

    entry_price = data.get(
        "entry_price",
        "غير واضح"
    )

    trend = data.get(
        "trend",
        "غير واضح"
    )

    short_trend = data.get(
        "short_trend",
        "غير واضح"
    )

    structure = data.get(
        "structure",
        "غير واضح"
    )

    keltner = data.get(
        "keltner",
        "غير واضح"
    )

    adx = data.get(
        "adx",
        "غير واضح"
    )

    candle = data.get(
        "candle",
        "غير واضح"
    )

    reason = data.get(
        "reason",
        "غير واضح"
    )

    cancellation = data.get(
        "cancellation",
        "غير واضح"
    )

    return (

        "🎓 تحليل زينو\n\n"

        f"🎯 Confidence: {confidence}%\n"

        f"📊 {asset} · ⏱ {timeframe}\n"

        "━━━━━━━━━━━━━━\n\n"

        f"🎯 القرار: {direction}\n"

        f"🕐 وقت الشارت: {chart_time}\n"

        f"⏰ وقت الدخول: {entry_time}\n"

        f"💵 سعر الدخول: {entry_price}\n"

        "━━━━━━━━━━━━━━\n\n"

        f"📐 الاتجاه: {trend}\n"

        f"📈 الاتجاه القصير: {short_trend}\n"

        f"🏗 البنية السعرية: {structure}\n"

        f"〽️ Keltner 20/10: {keltner}\n"

        f"📊 ADX 14/14: {adx}\n"

        f"🕯 شمعة التأكيد: {candle}\n"

        "━━━━━━━━━━━━━━\n\n"

        f"🧠 شرح زينو: {reason}\n\n"

        f"🛑 شرط إلغاء الإشارة: {cancellation}\n"

        "━━━━━━━━━━━━━━\n\n"

        "⚠️ التحليل مبني على الشارت المرسل فقط."
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    await update.message.reply_text(

        "🤖 ZINOSIGNASLQQ جاهز.\n\n"
        "📸 أرسل صورة الشارت للتحليل."
    )


# ============================================================
# STATS
# ============================================================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    total = wins + losses

    if total:

        winrate = (
            wins / total
        ) * 100

    else:

        winrate = 0

    await update.message.reply_text(

        "📊 إحصائيات التداول\n\n"

        f"✅ WIN: {wins}\n"

        f"❌ LOSS: {losses}\n"

        f"📌 TOTAL: {total}\n"

        f"🎯 WIN RATE: {winrate:.1f}%"
    )


# ============================================================
# RESET STATS
# ============================================================

async def reset_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global wins
    global losses

    if not is_owner(update):
        return

    wins = 0
    losses = 0

    await update.message.reply_text(
        "♻️ تم تصفير الإحصائيات."
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def photo_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    if not update.message:
        return

    if not update.message.photo:
        return

    status = await update.message.reply_text(
        "🔎 تحليل سريع..."
    )

    try:

        # أعلى جودة متاحة من Telegram
        photo = update.message.photo[-1]

        telegram_file = await photo.get_file()

        image_buffer = io.BytesIO()

        await telegram_file.download_to_memory(
            out=image_buffer
        )

        image_bytes = image_buffer.getvalue()

        if not image_bytes:

            raise ValueError(
                "الصورة فارغة"
            )

        data = await analyze_image(
            image_bytes
        )

        signal = format_signal(
            data
        )

        keyboard = InlineKeyboardMarkup(

            [
                [

                    InlineKeyboardButton(
                        "✅ WIN",
                        callback_data="trade_win"
                    ),

                    InlineKeyboardButton(
                        "❌ LOSS",
                        callback_data="trade_loss"
                    )

                ]
            ]
        )

        await status.edit_text(

            signal,

            reply_markup=keyboard
        )

    except Exception as e:

        error_text = str(e)

        if len(error_text) > 1200:

            error_text = error_text[:1200]

        await status.edit_text(

            "❌ فشل التحليل.\n\n"
            f"{error_text}"
        )


# ============================================================
# BUTTONS
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global wins
    global losses

    query = update.callback_query

    if not query:
        return

    if query.from_user.id != OWNER_ID:

        await query.answer()

        return

    await query.answer()

    if query.data == "trade_win":

        wins += 1

        await query.message.reply_text(

            "✅ تم تسجيل WIN\n\n"

            f"WIN: {wins}\n"

            f"LOSS: {losses}\n"

            f"TOTAL: {wins + losses}"
        )

    elif query.data == "trade_loss":

        losses += 1

        await query.message.reply_text(

            "❌ تم تسجيل LOSS\n\n"

            f"WIN: {wins}\n"

            f"LOSS: {losses}\n"

            f"TOTAL: {wins + losses}"
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    context
):

    print(
        "Telegram error:",
        context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    application = (

        ApplicationBuilder()

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
            "stats",
            stats
        )
    )

    application.add_handler(

        CommandHandler(
            "resetstats",
            reset_stats
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
            button_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "ZinoQuotexSignalAI started."
    )

    print(
        "Gemini model:",
        GEMINI_MODEL
    )

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
