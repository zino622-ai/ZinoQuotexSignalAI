import os
import io
import json
import base64
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import httpx
from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_RAW = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID_RAW:
    raise RuntimeError("OWNER_ID is missing")

if not GEMINI_MODEL:
    raise RuntimeError("GEMINI_MODEL is missing")

try:
    OWNER_ID = int(OWNER_ID_RAW)
except ValueError:
    raise RuntimeError("OWNER_ID must be an integer")


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# TIMEZONE UTC-3
# ============================================================

UTC_MINUS_3 = timezone(timedelta(hours=-3))


# ============================================================
# GEMINI PROMPT
# ============================================================

ANALYSIS_PROMPT = r"""
أنت محلل تقني متخصص في تحليل صور شارت Quotex قصيرة المدى.

مهمتك تحليل الصورة وإعطاء اتجاه واحد فقط:

UP أو DOWN.

لا تقل NO SIGNAL.
لا تقل WAIT.
لا تترك القرار محايداً.

حتى إذا كانت الصورة غير مثالية، اختر الاتجاه الذي تدعمه الأدلة الأقوى، مع تخفيض Confidence إذا كانت الأدلة ضعيفة.

━━━━━━━━━━━━━━━━━━
1. PRICE ACTION — الأولوية القصوى
━━━━━━━━━━━━━━━━━━

حلل الشموع الأخيرة بدقة:

- Open
- Close
- High
- Low
- حجم جسم الشمعة
- طول الـ upper/lower wick
- القمم والقيعان
- قوة الإغلاق

حدد:

UPTREND:
Higher High + Higher Low

DOWNTREND:
Lower High + Lower Low

CONSOLIDATION:
حركة جانبية بدون اتجاه واضح.

Price Action أهم من أي مؤشر منفرد.

━━━━━━━━━━━━━━━━━━
2. MARKET STRUCTURE
━━━━━━━━━━━━━━━━━━

ابحث عن:

- Break of Structure
- Higher High
- Higher Low
- Lower High
- Lower Low
- Breakout
- Failed Breakout
- Liquidity Sweep
- Rejection

إذا أغلقت شمعة قوية فوق مستوى:
يدعم UP.

إذا أغلقت شمعة قوية تحت مستوى:
يدعم DOWN.

إذا حدث اختراق ثم رجع السعر وأغلق داخل المنطقة:
اعتبره Failed Breakout / Rejection.

━━━━━━━━━━━━━━━━━━
3. CANDLE CONFIRMATION
━━━━━━━━━━━━━━━━━━

راقب:

BULLISH:
- Bullish Engulfing
- Hammer
- Bullish rejection
- Strong bullish close
- Strong breakout candle

BEARISH:
- Bearish Engulfing
- Shooting Star
- Bearish rejection
- Strong bearish close
- Strong breakdown candle

الأولوية للشمعة المغلقة فعلياً وليس شمعة مازالت تتشكل.

━━━━━━━━━━━━━━━━━━
4. ADX + DMI
━━━━━━━━━━━━━━━━━━

استخدم ADX لقياس قوة الاتجاه.

ADX أقل من 20:
اتجاه ضعيف أو سوق جانبي.

ADX فوق 20:
اتجاه محتمل.

ADX فوق 25:
قوة اتجاه أعلى.

ADX صاعد:
قوة الاتجاه تزداد.

ADX هابط:
الاتجاه يضعف.

DMI:

+DI > -DI:
يدعم UP.

-DI > +DI:
يدعم DOWN.

ADX/DMI لا يقرر وحده.

━━━━━━━━━━━━━━━━━━
5. KELTNER CHANNEL
━━━━━━━━━━━━━━━━━━

استخدم Keltner كفلتر.

راقب:

- Upper Band
- Middle Band
- Lower Band
- Breakout
- Rejection
- Position of price

UP:
السعر فوق Middle Band مع momentum صاعد وPrice Action داعم.

DOWN:
السعر تحت Middle Band مع momentum هابط وPrice Action داعم.

لا تعتبر لمس Upper Band سبباً تلقائياً لـ DOWN.

ولا تعتبر لمس Lower Band سبباً تلقائياً لـ UP.

━━━━━━━━━━━━━━━━━━
6. RSI
━━━━━━━━━━━━━━━━━━

RSI فلتر ثانوي.

RSI فوق 70:
Overbought محتمل.

RSI تحت 30:
Oversold محتمل.

لكن لا تعاكس اتجاه قوي بسبب RSI وحده.

راقب أيضاً:
- Momentum
- Divergence
- خروج RSI من extreme zone

━━━━━━━━━━━━━━━━━━
7. BREAKOUT
━━━━━━━━━━━━━━━━━━

إذا كان السوق في Consolidation:

حدد أعلى وأدنى النطاق.

إغلاق قوي فوق النطاق:
يدعم UP.

إغلاق قوي تحت النطاق:
يدعم DOWN.

تحقق من:

1. قوة الإغلاق
2. Market Structure
3. ADX
4. DMI
5. Keltner
6. Failed Breakout

wick فقط لا يعتبر Breakout مؤكداً.

━━━━━━━━━━━━━━━━━━
8. LIQUIDITY SWEEP
━━━━━━━━━━━━━━━━━━

إذا أخذ السعر Low سابق ثم عاد وأغلق فوقه:
Bullish rejection محتمل.

إذا أخذ السعر High سابق ثم عاد وأغلق تحته:
Bearish rejection محتمل.

لا تعتمد على wick وحده.
ابحث عن confirmation.

━━━━━━━━━━━━━━━━━━
9. PRIORITY
━━━━━━━━━━━━━━━━━━

عند تعارض المؤشرات:

1. Price Action
2. Market Structure
3. Candle Close
4. Breakout / Rejection
5. ADX + DMI
6. Keltner
7. RSI

RSI لا يقلب إشارة قوية بمفرده.

━━━━━━━━━━━━━━━━━━
10. ENTRY DELAY
━━━━━━━━━━━━━━━━━━

حدد أفضل وقت للدخول.

يمكن اختيار:

1 دقيقة
أو
2 دقيقة
أو
3 دقائق

إذا كانت الحركة واضحة:
1 دقيقة.

إذا احتاجت Confirmation:
2 دقيقة.

إذا كان السعر عند مستوى مهم ويحتاج شمعة إضافية:
3 دقائق.

لا تختار التأخير عشوائياً.

━━━━━━━━━━━━━━━━━━
11. TIMEFRAME
━━━━━━━━━━━━━━━━━━

استخرج Timeframe الظاهر في الصورة.

إذا كان 1M:
استخدم 1M.

إذا كان 2M:
استخدم 2M.

لا تخترع Timeframe غير ظاهر.

━━━━━━━━━━━━━━━━━━
12. ENTRY PRICE
━━━━━━━━━━━━━━━━━━

حدد سعر الدخول المتوقع.

استخدم سعر واضح ومناسب للعرض في Quotex.

لا تستخدم أرقاماً عشرية طويلة بلا داعٍ.

الحد الأقصى 6 أرقام عشرية.

━━━━━━━━━━━━━━━━━━
13. CANCELLATION LEVEL
━━━━━━━━━━━━━━━━━━

UP:

إلغاء إذا أغلقت شمعة تحت مستوى الإلغاء.

DOWN:

إلغاء إذا أغلقت شمعة فوق مستوى الإلغاء.

المستوى يجب أن يكون مرتبطاً بـ swing أو structure مهم.

لا تضع رقماً عشوائياً.

━━━━━━━━━━━━━━━━━━
14. CONFIDENCE
━━━━━━━━━━━━━━━━━━

50–59:
أدلة ضعيفة.

60–69:
تأكيد متوسط.

70–79:
عدة أدلة متوافقة.

80–89:
توافق قوي جداً.

90+:
فقط عندما تكون الأدلة استثنائية ومتوافقة بوضوح.

لا تعطِ 90% أو 95% أو 98% لمجرد توافق عادي.

━━━━━━━━━━━━━━━━━━
15. IMPORTANT
━━━━━━━━━━━━━━━━━━

لا تقل:

مضمونة
100%
لا تخسر
مؤكد

لا تحاول جعل UP وDOWN متساويين.

اتبع الأدلة الموجودة في الصورة.

━━━━━━━━━━━━━━━━━━
16. OUTPUT
━━━━━━━━━━━━━━━━━━

أرجع JSON صالح فقط.

الشكل:

{
  "asset": "USD/ZAR",
  "timeframe": "2M",
  "direction": "UP",
  "confidence": 72,
  "entry_delay_minutes": 2,
  "entry_price": "14.868390",
  "cancellation_level": "14.866500",
  "cancellation_rule": "close_below",
  "trend": "Bullish",
  "market_structure": "Higher High + Higher Low",
  "momentum": "Bullish",
  "confirmation": "Bullish candle close",
  "adx_dmi": "ADX rising, +DI above -DI",
  "keltner": "Price above middle band",
  "rsi": "Neutral/Bullish",
  "reason": "Bullish structure with confirmed breakout and supporting momentum."
}

القيم يجب أن تكون مبنية على الصورة فقط.

لا تكتب Markdown.

لا تكتب أي كلام خارج JSON.
"""


# ============================================================
# OWNER
# ============================================================

def is_owner(update: Update) -> bool:
    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


# ============================================================
# GEMINI
# ============================================================

async def analyze_image(image_bytes: bytes) -> dict:

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )

    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": ANALYSIS_PROMPT
                    },
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": image_base64,
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        }
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    timeout = httpx.Timeout(
        connect=15.0,
        read=90.0,
        write=30.0,
        pool=15.0,
    )

    async with httpx.AsyncClient(timeout=timeout) as client:

        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

    if response.status_code != 200:

        try:
            error_data = response.json()

            message = (
                error_data
                .get("error", {})
                .get("message", response.text)
            )

        except Exception:
            message = response.text

        raise RuntimeError(
            f"Gemini HTTP {response.status_code}: {message}"
        )

    data = response.json()

    try:

        text = (
            data["candidates"][0]
            ["content"]["parts"][0]["text"]
        )

    except Exception:

        raise RuntimeError(
            "Gemini returned an empty response"
        )

    text = text.strip()

    if text.startswith("```"):

        text = text.replace(
            "```json",
            ""
        )

        text = text.replace(
            "```",
            ""
        )

        text = text.strip()

    try:

        result = json.loads(text)

    except json.JSONDecodeError:

        raise RuntimeError(
            "Gemini returned invalid JSON"
        )

    return result


# ============================================================
# VALIDATE
# ============================================================

def validate_signal(data: dict) -> dict:

    required = [
        "asset",
        "timeframe",
        "direction",
        "confidence",
        "entry_delay_minutes",
        "entry_price",
        "cancellation_level",
        "cancellation_rule",
        "trend",
        "market_structure",
        "momentum",
        "confirmation",
        "adx_dmi",
        "keltner",
        "rsi",
        "reason",
    ]

    for key in required:

        if key not in data:

            raise RuntimeError(
                f"Gemini response missing: {key}"
            )

    direction = str(
        data["direction"]
    ).upper()

    if direction not in ("UP", "DOWN"):

        raise RuntimeError(
            "Invalid direction"
        )

    try:

        confidence = int(
            data["confidence"]
        )

    except Exception:

        raise RuntimeError(
            "Invalid confidence"
        )

    if confidence < 0 or confidence > 100:

        raise RuntimeError(
            "Invalid confidence range"
        )

    try:

        delay = int(
            data["entry_delay_minutes"]
        )

    except Exception:

        raise RuntimeError(
            "Invalid entry delay"
        )

    if delay not in (1, 2, 3):

        raise RuntimeError(
            "Entry delay must be 1, 2 or 3 minutes"
        )

    rule = str(
        data["cancellation_rule"]
    ).lower()

    if direction == "UP":

        if rule != "close_below":

            raise RuntimeError(
                "Invalid cancellation rule for UP"
            )

    else:

        if rule != "close_above":

            raise RuntimeError(
                "Invalid cancellation rule for DOWN"
            )

    data["direction"] = direction
    data["confidence"] = confidence
    data["entry_delay_minutes"] = delay
    data["cancellation_rule"] = rule

    return data


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(data: dict) -> str:

    direction = data["direction"]

    if direction == "UP":

        direction_text = "🟢 UP — شراء (Call)"

        cancel_text = (
            f"🛑 إلغاء إذا أغلقت شمعة تحت "
            f"{data['cancellation_level']}"
        )

        trend_emoji = "📈"

    else:

        direction_text = "🔴 DOWN — بيع (Put)"

        cancel_text = (
            f"🛑 إلغاء إذا أغلقت شمعة فوق "
            f"{data['cancellation_level']}"
        )

        trend_emoji = "📉"

    now = datetime.now(
        UTC_MINUS_3
    )

    entry_time = now + timedelta(
        minutes=int(
            data["entry_delay_minutes"]
        )
    )

    entry_time_text = entry_time.strftime(
        "%H:%M"
    )

    return (
        "🎓 تحليل زينو\n\n"

        f"🎯 Confidence: "
        f"{data['confidence']}%\n"

        f"📊 {data['asset']} · "
        f"⏱ {data['timeframe']}\n"

        "━━━━━━━━━━━━━━\n\n"

        f"🎯 القرار: "
        f"{direction_text}\n"

        f"🕐 وقت الدخول: "
        f"{entry_time_text}\n"

        f"⏳ بعد: "
        f"{data['entry_delay_minutes']} دقيقة\n\n"

        f"💵 سعر الدخول: "
        f"{data['entry_price']}\n\n"

        f"{cancel_text}\n"

        "━━━━━━━━━━━━━━\n\n"

        f"{trend_emoji} الاتجاه: "
        f"{data['trend']}\n"

        f"🏗 Market Structure: "
        f"{data['market_structure']}\n"

        f"💨 Momentum: "
        f"{data['momentum']}\n"

        f"🕯 Confirmation: "
        f"{data['confirmation']}\n"

        f"📊 ADX/DMI: "
        f"{data['adx_dmi']}\n"

        f"〽️ Keltner: "
        f"{data['keltner']}\n"

        f"📉 RSI: "
        f"{data['rsi']}\n\n"

        f"🧠 السبب:\n"
        f"{data['reason']}"
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

    keyboard = [
        [
            InlineKeyboardButton(
                "🎯 Get Signal",
                callback_data="get_signal",
            )
        ]
    ]

    await update.message.reply_text(

        "🎓 ZinoQuotexSignalAI\n\n"

        "📸 أرسل Screenshot للشارت "
        "ثم اضغط Get Signal.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# ============================================================
# PHOTO
# ============================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    if not update.message:
        return

    if not update.message.photo:
        return

    photo = update.message.photo[-1]

    file = await context.bot.get_file(
        photo.file_id
    )

    image_buffer = io.BytesIO()

    await file.download_to_memory(
        image_buffer
    )

    image_buffer.seek(0)

    context.user_data["chart_image"] = (
        image_buffer.getvalue()
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🎯 Get Signal",
                callback_data="get_signal",
            )
        ]
    ]

    await update.message.reply_text(

        "✅ تم استلام الشارت.\n\n"
        "اضغط Get Signal للتحليل.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# ============================================================
# GET SIGNAL
# ============================================================

async def get_signal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:
        return

    if query.from_user.id != OWNER_ID:

        await query.answer()

        return

    await query.answer()

    image_bytes = context.user_data.get(
        "chart_image"
    )

    if not image_bytes:

        await query.message.reply_text(
            "❌ أرسل Screenshot للشارت أولاً."
        )

        return

    processing_message = (
        await query.message.reply_text(
            "🔎 جاري تحليل الشارت..."
        )
    )

    try:

        result = await analyze_image(
            image_bytes
        )

        result = validate_signal(
            result
        )

        signal_text = format_signal(
            result
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "🎯 Get Signal",
                    callback_data="get_signal",
                )
            ]
        ]

        await processing_message.edit_text(

            signal_text,

            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

    except Exception as e:

        logger.exception(
            "Analysis error"
        )

        await processing_message.edit_text(

            f"❌ حدث خطأ أثناء التحليل:\n\n"
            f"{str(e)}"
        )


# ============================================================
# TEXT
# ============================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    if not update.message:
        return

    await update.message.reply_text(
        "📸 أرسل Screenshot للشارت أولاً."
    )


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

async def health(request):

    return web.Response(
        text="ZinoQuotexSignalAI is running"
    )


async def start_health_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    app.router.add_get(
        "/health",
        health
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT,
    )

    await site.start()

    logger.info(
        f"HTTP server started on port {PORT}"
    )

    return runner


# ============================================================
# MAIN
# ============================================================

async def main():

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
        CallbackQueryHandler(
            get_signal,
            pattern="^get_signal$",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    health_runner = (
        await start_health_server()
    )

    try:

        await application.initialize()

        await application.start()

        await application.updater.start_polling(
            drop_pending_updates=True
        )

        logger.info(
            "ZinoQuotexSignalAI started successfully"
        )

        await asyncio.Event().wait()

    finally:

        await application.updater.stop()

        await application.stop()

        await application.shutdown()

        await health_runner.cleanup()


if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped"
)
