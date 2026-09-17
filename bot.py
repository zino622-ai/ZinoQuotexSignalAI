import os
import re
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
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
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")

OWNER_ID = int(OWNER_ID)


# ============================================================
# GEMINI
# ============================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# TIMEZONE
# ============================================================

# Algeria time
ALGERIA_TZ = ZoneInfo("Africa/Algiers")

# Quotex timezone requested by user
# UTC-3
QUOTEX_TZ = ZoneInfo("Etc/GMT+3")


# ============================================================
# STATS
# ============================================================

wins = 0
losses = 0


# ============================================================
# OWNER
# ============================================================

def is_owner(update: Update) -> bool:
    if not update.effective_user:
        return False

    return update.effective_user.id == OWNER_ID


# ============================================================
# QUOTEX TIME
# ============================================================

def get_quotex_now():
    return datetime.now(QUOTEX_TZ)


def calculate_entry_time(delay_minutes: int):

    now = get_quotex_now()

    future = now + timedelta(
        minutes=delay_minutes
    )

    # Beginning of the selected candle
    return future.replace(
        second=0,
        microsecond=0
    )


# ============================================================
# ANALYSIS PROMPT
# ============================================================

ANALYSIS_PROMPT = """
حلل صورة الشارت المرفقة كمتداول محترف للتحليل قصير المدى.

مهم جدًا:

لا تفترض أن الإطار الزمني M1.
اقرأ الإطار الزمني من الشارت إذا كان ظاهرًا.
إذا لم يكن ظاهرًا اكتب: غير واضح.

يجب أن تعطي اتجاهًا واحدًا فقط:

CALL (UP)
أو
PUT (DOWN)

لا تكتب:
NO SIGNAL
NEUTRAL
WAIT

الاستراتيجية:

1. الاتجاه الرئيسي:
- Higher Highs + Higher Lows = اتجاه صاعد = أفضلية CALL
- Lower Highs + Lower Lows = اتجاه هابط = أفضلية PUT

2. إذا كان السوق يتحرك أفقيًا:
حدد منطقة الحركة واستعمل رد فعل السعر من الحدود.

3. الدعم والمقاومة:
- قرب SUPPORT ابحث عن CALL بعد تأكيد صاعد.
- قرب RESISTANCE ابحث عن PUT بعد تأكيد هابط.
- لا تدخل مباشرة عند لمس المستوى.
- انتظر تأكيد الشمعة.

4. Price Action:
افحص:
- Hammer
- Pin Bar
- Bullish Engulfing
- Bearish Engulfing
- Rejection wick
- Momentum candle
- Breakout + confirmation

5. RSI:
إذا كان ظاهرًا:
- فوق 70 = تشبع شراء وقد يدعم PUT
- تحت 30 = تشبع بيع وقد يدعم CALL

لا تخترع RSI إذا لم يكن ظاهرًا.

6. EMA 5 و EMA 13:
إذا كانا ظاهرين:
- EMA 5 فوق EMA 13 يدعم CALL
- EMA 5 تحت EMA 13 يدعم PUT
- التقاطع الواضح يقوي الإشارة

لا تخترع قيم EMA.

لا تستخدم:
MACD
Stochastic
Fibonacci
ZigZag

الأولوية:

Market Structure
+
Support/Resistance reaction
+
Price Action
+
Momentum
+
Confirmation Candle
+
RSI/EMA إذا كانت ظاهرة

====================================================
أهم شيء: وقت الدخول
====================================================

لا تقل دائمًا بعد دقيقة.

أنت من يقرر بعد كم دقيقة تكون أفضل نقطة دخول.

استعمل:

1 دقيقة:
إذا كانت الإشارة مؤكدة والسعر جاهز للدخول.

2 دقيقة:
إذا كان يحتاج تأكيد بسيط.

3 دقائق:
إذا كان يحتاج شمعة تأكيد أو إعادة اختبار.

4 دقائق:
إذا كان يجب انتظار حركة أو تأكيد أقوى.

5 دقائق:
إذا كان الدخول الحالي مبكرًا ويجب الانتظار.

اختر من 1 إلى 5 دقائق حسب الشارت الحقيقي.

وقت الدخول يجب أن يكون في بداية الشمعة المستقبلية المختارة.

====================================================
مهم جدًا
====================================================

في نهاية التحليل اكتب هذه المعلومات بصيغة واضحة:

ENTRY: 3 MINUTES

إذا اخترت دقيقتين:

ENTRY: 2 MINUTES

إذا اخترت دقيقة:

ENTRY: 1 MINUTE

إذا اخترت 4:

ENTRY: 4 MINUTES

إذا اخترت 5:

ENTRY: 5 MINUTES

لا تكتب وقت انتهاء.

====================================================
صيغة التحليل
====================================================

🎯 الإشارة: CALL (UP) 72%

📊 نسبة الثقة: 72%
📊 الأصل: ...
📊 الإطار الزمني: ...
🧭 الاتجاه: ...
📈 الاتجاه القصير: ...

🏗 Market Structure:
...

⚡ Momentum:
...

📊 Price Action:
...

🕯 شمعة التأكيد:
...

💡 السبب:
...

ENTRY: 3 MINUTES

يجب أن تكون قيمة ENTRY من 1 إلى 5 حسب التحليل الحقيقي.
"""


# ============================================================
# EXTRACT SIGNAL
# ============================================================

def extract_signal(text: str):

    upper = text.upper()

    # Prefer explicit signal
    patterns = [
        r"SIGNAL\s*:\s*(CALL|PUT)",
        r"الإشارة\s*[:：]\s*(?:🟢\s*)?(CALL|PUT)",
        r"(CALL|PUT)\s*\(?(?:UP|DOWN)\)?",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            upper,
            re.IGNORECASE
        )

        if match:
            return match.group(1).upper()

    return None


# ============================================================
# EXTRACT CONFIDENCE
# ============================================================

def extract_confidence(text: str):

    patterns = [
        r"CONFIDENCE\s*:\s*(\d{1,3})",
        r"نسبة\s*الثقة\s*[:：]?\s*(\d{1,3})",
        r"الثقة\s*[:：]?\s*(\d{1,3})",
        r"(?:CALL|PUT)\s*\([^)]+\)\s*(\d{1,3})\s*%",
        r"(?:CALL|PUT)\s+(\d{1,3})\s*%",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            value = int(match.group(1))

            if 50 <= value <= 95:
                return value

    return None


# ============================================================
# EXTRACT ENTRY DELAY
# ============================================================

def extract_entry_delay(text: str):

    # --------------------------------------------------------
    # 1. Preferred format
    # --------------------------------------------------------

    patterns = [

        r"ENTRY\s*:\s*(\d+)\s*MINUTES?",

        r"ENTRY_DELAY_MINUTES\s*:\s*(\d+)",

        r"ENTRY_DELAY\s*:\s*(\d+)",

        r"ENTRY\s*TIME\s*:\s*(\d+)\s*MINUTES?",

        # Arabic
        r"الدخول\s*:\s*(?:بعد\s*)?(\d+)\s*دقائق?",

        r"وقت\s*الدخول\s*:\s*(?:بعد\s*)?(\d+)\s*دقائق?",

        r"بعد\s*(\d+)\s*دقائق?",

        r"بعد\s*(\d+)\s*دقيقة",

        r"دخول\s*بعد\s*(\d+)",

        # English
        r"ENTER\s*AFTER\s*(\d+)\s*MINUTES?",

        r"ENTRY\s*AFTER\s*(\d+)\s*MINUTES?",

        r"IN\s*(\d+)\s*MINUTES?",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            value = int(match.group(1))

            if 1 <= value <= 5:
                return value

    return None


# ============================================================
# CLEAN ANALYSIS
# ============================================================

def clean_analysis(text: str):

    lines = text.splitlines()

    cleaned = []

    for line in lines:

        stripped = line.strip()

        # Remove machine/control lines
        if re.match(
            r"ENTRY\s*:",
            stripped,
            re.IGNORECASE
        ):
            continue

        if re.match(
            r"ENTRY_DELAY",
            stripped,
            re.IGNORECASE
        ):
            continue

        if re.match(
            r"SIGNAL\s*:",
            stripped,
            re.IGNORECASE
        ):
            continue

        if re.match(
            r"CONFIDENCE\s*:",
            stripped,
            re.IGNORECASE
        ):
            continue

        cleaned.append(line)

    result = "\n".join(cleaned).strip()

    # Remove expiry if Gemini accidentally writes one
    result = re.sub(
        r"(?im)^.*(?:expiry|expiration|انتهاء|الانتهاء).*$\n?",
        "",
        result
    )

    return result.strip()


# ============================================================
# GEMINI ANALYSIS
# ============================================================

async def analyze_chart(image_bytes: bytes):

    try:

        response = await asyncio.to_thread(
            client.models.generate_content,

            model=GEMINI_MODEL,

            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg"
                ),
                ANALYSIS_PROMPT,
            ],

            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=550,
            ),
        )

        text = (response.text or "").strip()

        if not text:
            raise RuntimeError(
                "Gemini returned an empty response"
            )

        signal = extract_signal(text)

        if signal not in ("CALL", "PUT"):
            raise RuntimeError(
                "Gemini did not return CALL or PUT"
            )

        confidence = extract_confidence(text)

        if confidence is None:

            # Try to get any reasonable percentage
            percentages = re.findall(
                r"(\d{2,3})\s*%",
                text
            )

            valid = [
                int(x)
                for x in percentages
                if 50 <= int(x) <= 95
            ]

            if valid:
                confidence = valid[0]

        if confidence is None:
            confidence = 65

        entry_delay = extract_entry_delay(text)

        # ----------------------------------------------------
        # IMPORTANT:
        # Do NOT blindly default to 1 minute.
        #
        # If Gemini forgot the ENTRY line completely,
        # use 2 minutes as a safe parser fallback rather
        # than pretending the setup is ready in 1 minute.
        # ----------------------------------------------------

        if entry_delay is None:
            entry_delay = 2

        analysis = clean_analysis(text)

        return {
            "analysis": analysis,
            "signal": signal,
            "confidence": confidence,
            "entry_delay": entry_delay,
        }

    except Exception as e:

        error_text = str(e).lower()

        # Temporary Gemini errors
        if any(
            code in error_text
            for code in [
                "503",
                "429",
                "500",
                "502",
                "504",
                "unavailable",
                "overloaded",
                "high demand",
            ]
        ):

            raise RuntimeError(
                "Gemini مشغول حاليًا. "
                "عاود إرسال الشارت بعد لحظات."
            )

        raise


# ============================================================
# FINAL FORMAT
# ============================================================

def format_result(
    result,
    entry_time
):

    signal = result["signal"]
    confidence = result["confidence"]
    delay = result["entry_delay"]

    if signal == "CALL":

        signal_display = (
            f"🟢 CALL (UP) {confidence}%"
        )

    else:

        signal_display = (
            f"🔴 PUT (DOWN) {confidence}%"
        )

    return (
        f"🎯 الإشارة: {signal_display}\n\n"

        f"📊 نسبة الثقة: {confidence}%\n"
        f"⏱️ وقت الدخول: {entry_time.strftime('%H:%M')}\n"
        f"⏳ بعد: {delay} دقيقة\n\n"

        f"{result['analysis']}"
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "⛔ هذا البوت خاص."
        )

        return

    status = await update.message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:

        # Get largest Telegram photo
        photo = update.message.photo[-1]

        telegram_file = await photo.get_file()

        # Download original Telegram image bytes.
        # NO RESIZE.
        # NO PIL.
        # NO quality conversion.
        image_bytes = bytes(
            await telegram_file.download_as_bytearray()
        )

        result = await analyze_chart(
            image_bytes
        )

        entry_time = calculate_entry_time(
            result["entry_delay"]
        )

        final_message = format_result(
            result,
            entry_time
        )

        await status.edit_text(
            final_message
        )

    except Exception as e:

        await status.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{str(e)}"
        )


# ============================================================
# START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "⛔ هذا البوت خاص."
        )

        return

    await update.message.reply_text(
        "🤖 ZinoQuotexSignalAI\n\n"
        "📸 أرسل Screenshot للشارت.\n\n"
        "البوت يحدد:\n"
        "🎯 CALL / PUT\n"
        "📊 نسبة الثقة\n"
        "⏱️ وقت الدخول\n"
        "📈 الاتجاه\n"
        "🕯️ تأكيد الشمعة\n\n"
        "لا يفترض M1 تلقائيًا."
    )


# ============================================================
# HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):

        await update.message.reply_text(
            "⛔ هذا البوت خاص."
        )

        return

    await update.message.reply_text(
        "📖 طريقة الاستخدام:\n\n"
        "1️⃣ افتح Quotex\n"
        "2️⃣ خذ Screenshot للشارت\n"
        "3️⃣ أرسل الصورة للبوت\n\n"
        "البوت يحلل:\n"
        "• Market Structure\n"
        "• Trend\n"
        "• Support / Resistance\n"
        "• Price Action\n"
        "• Confirmation Candle\n"
        "• RSI إذا كان ظاهرًا\n"
        "• EMA 5/13 إذا كانت ظاهرة\n\n"
        "⏱️ وقت الدخول يحدده حسب الشارت.\n"
        "ليس دائمًا بعد دقيقة.\n\n"
        "🕐 التوقيت: Quotex UTC-3"
    )


# ============================================================
# WIN
# ============================================================

async def win_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global wins

    if not is_owner(update):
        return

    wins += 1

    total = wins + losses

    rate = (
        wins / total * 100
        if total
        else 0
    )

    await update.message.reply_text(
        f"✅ WIN\n\n"
        f"🏆 Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"📊 Total: {total}\n"
        f"📈 Win Rate: {rate:.1f}%"
    )


# ============================================================
# LOSS
# ============================================================

async def loss_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global losses

    if not is_owner(update):
        return

    losses += 1

    total = wins + losses

    rate = (
        wins / total * 100
        if total
        else 0
    )

    await update.message.reply_text(
        f"❌ LOSS\n\n"
        f"🏆 Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"📊 Total: {total}\n"
        f"📈 Win Rate: {rate:.1f}%"
    )


# ============================================================
# STATS
# ============================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_owner(update):
        return

    total = wins + losses

    rate = (
        wins / total * 100
        if total
        else 0
    )

    await update.message.reply_text(
        f"📊 ZinoQuotexSignalAI\n\n"
        f"🏆 Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"📊 Total: {total}\n"
        f"📈 Win Rate: {rate:.1f}%"
    )


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-type",
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


def run_health_server():

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


# ============================================================
# MAIN
# ============================================================

def main():

    # Render health server
    health_thread = Thread(
        target=run_health_server,
        daemon=True
    )

    health_thread.start()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
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
            handle_photo
        )
    )

    print(
        "ZinoQuotexSignalAI is running..."
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
