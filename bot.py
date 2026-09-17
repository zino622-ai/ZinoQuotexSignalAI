import os
import re
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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

from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID:
    raise RuntimeError("OWNER_ID is missing")


OWNER_ID = int(OWNER_ID)


# ============================================================
# GEMINI CLIENT
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# TIME ZONES
# ============================================================

ALGERIA_TZ = ZoneInfo("Africa/Algiers")

# Quotex timezone requested by the user: UTC-3
QUOTEX_TZ = ZoneInfo("Etc/GMT+3")


# ============================================================
# SIMPLE STATS
# ============================================================

wins = 0
losses = 0


# ============================================================
# OWNER CHECK
# ============================================================

def is_owner(update: Update) -> bool:
    if not update.effective_user:
        return False

    return update.effective_user.id == OWNER_ID


# ============================================================
# TIME FUNCTIONS
# ============================================================

def get_quotex_now():
    return datetime.now(QUOTEX_TZ)


def calculate_entry_time(minutes_ahead: int):
    """
    Calculates the beginning of the future candle.

    Example:
    current time 21:17
    delay 3
    entry = 21:20
    """

    now = get_quotex_now()

    future = now + timedelta(minutes=minutes_ahead)

    entry = future.replace(
        second=0,
        microsecond=0
    )

    return entry


# ============================================================
# ANALYSIS PROMPT
# ============================================================

ANALYSIS_PROMPT = r"""
You are an advanced technical analyst for short-term binary-options style chart analysis.

Analyze ONLY the attached chart screenshot.

IMPORTANT:

1. Do NOT assume the timeframe is M1.
   Read the timeframe from the chart if visible.
   If the timeframe is visible, use it.
   If it is not visible, write "غير واضح".

2. You MUST give either:
   CALL
   or
   PUT

Never answer:
NO SIGNAL
NEUTRAL
WAIT

3. The analysis must determine the best practical entry moment.

Do NOT automatically choose 1 minute.

Choose ENTRY_DELAY_MINUTES intelligently:

1 = price is already confirmed and entry can be taken at the next appropriate candle.
2 = a little confirmation is still needed.
3 = stronger confirmation is preferred.
4 = significant confirmation/waiting is needed.
5 = wait for a clearer setup.

The delay must be between 1 and 5 minutes.

The entry should be at the BEGINNING of that future candle.

4. Main strategy:

UPTREND:
- Higher Highs
- Higher Lows
- Prefer CALL

DOWNTREND:
- Lower Highs
- Lower Lows
- Prefer PUT

CONSOLIDATION:
- Identify horizontal movement between boundaries.
- Consider reactions from support/resistance.
- Do not blindly follow the middle of the range.

5. Support / Resistance:

Use visible support and resistance as part of the reasoning.

Near SUPPORT:
Look for CALL only after bullish confirmation.

Near RESISTANCE:
Look for PUT only after bearish confirmation.

Do NOT enter immediately when price touches a level.
Wait for candle confirmation.

6. Candlestick confirmation:

Look for:
- Hammer
- Pin Bar
- Bullish Engulfing
- Bearish Engulfing
- Strong rejection wick
- Strong momentum candle
- Break and confirmation

Hammer / bullish rejection:
Long lower wick + small body can support CALL.

Bearish rejection:
Long upper wick + small body can support PUT.

Bullish engulfing:
Supports CALL when appearing in the correct location.

Bearish engulfing:
Supports PUT when appearing in the correct location.

7. RSI:

If RSI is visible:
- Above 70 = overbought, possible downside/reversal
- Below 30 = oversold, possible upside/reversal

Do NOT invent RSI values if RSI is not visible.

8. EMA 5 and EMA 13:

If EMA 5 and EMA 13 are visible:
- EMA 5 above EMA 13 supports CALL
- EMA 5 below EMA 13 supports PUT
- A clear crossover can strengthen the signal

Do NOT invent EMA values if they are not visible.

9. Do NOT use:
- MACD
- Stochastic
- Fibonacci
- ZigZag
- Random indicators not visible on the chart

10. Priority:

Market Structure
+
Support/Resistance reaction
+
Liquidity/rejection
+
Momentum
+
Candlestick confirmation
+
RSI/EMA only when visible

11. Confidence:

Give a realistic confidence percentage.

Do not always use high confidence.

Example:
60
68
74
81

12. VERY IMPORTANT MACHINE-READABLE LINES:

At the end of your answer, you MUST include EXACTLY these three lines:

SIGNAL: CALL

CONFIDENCE: 72

ENTRY_DELAY_MINUTES: 3

Replace the values according to your actual analysis.

SIGNAL must be exactly CALL or PUT.

CONFIDENCE must be a number from 50 to 95.

ENTRY_DELAY_MINUTES must be an integer from 1 to 5.

Do not omit these lines.

13. Keep the analysis concise.

Use this format:

🎯 الإشارة: 🟢 CALL (UP) 72%

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

SIGNAL: CALL
CONFIDENCE: 72
ENTRY_DELAY_MINUTES: 3
"""


# ============================================================
# EXTRACT SIGNAL
# ============================================================

def extract_signal(text: str):
    """
    Extract CALL or PUT from the dedicated SIGNAL line.
    """

    match = re.search(
        r"SIGNAL\s*:\s*(CALL|PUT)",
        text,
        re.IGNORECASE
    )

    if match:
        return match.group(1).upper()

    # Fallback if Gemini forgot the machine-readable line
    upper = text.upper()

    if re.search(r"\bCALL\b", upper):
        return "CALL"

    if re.search(r"\bPUT\b", upper):
        return "PUT"

    return None


# ============================================================
# EXTRACT CONFIDENCE
# ============================================================

def extract_confidence(text: str):
    match = re.search(
        r"CONFIDENCE\s*:\s*(\d{1,3})",
        text,
        re.IGNORECASE
    )

    if match:
        value = int(match.group(1))

        if 50 <= value <= 95:
            return value

    # Fallback for Arabic / normal confidence text
    patterns = [
        r"نسبة الثقة\s*[:：]?\s*(\d{1,3})",
        r"الثقة\s*[:：]?\s*(\d{1,3})",
        r"(\d{1,3})\s*%",
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
    """
    Extract ENTRY_DELAY_MINUTES.

    IMPORTANT:
    Never silently default to 1.
    """

    match = re.search(
        r"ENTRY_DELAY_MINUTES\s*:\s*(\d+)",
        text,
        re.IGNORECASE
    )

    if match:
        value = int(match.group(1))

        if 1 <= value <= 5:
            return value

    # Fallback patterns
    patterns = [
        r"دقائق\s*[:：]?\s*(\d+)",
        r"بعد\s*(\d+)\s*دقائق",
        r"بعد\s*(\d+)\s*دقيقة",
        r"دخول\s*بعد\s*(\d+)",
        r"entry\s*(?:after|in)\s*(\d+)\s*minutes?",
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
# CLEAN GEMINI RESULT
# ============================================================

def clean_result(
    text: str,
    signal: str,
    confidence: int,
    entry_delay: int
):
    """
    Removes machine-readable lines from the visible output.
    """

    lines = text.splitlines()

    cleaned = []

    for line in lines:

        stripped = line.strip()

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

        if re.match(
            r"ENTRY_DELAY_MINUTES\s*:",
            stripped,
            re.IGNORECASE
        ):
            continue

        cleaned.append(line)

    result = "\n".join(cleaned).strip()

    # Remove accidental old entry-time lines from Gemini
    result = re.sub(
        r"(?im)^.*وقت الدخول.*$\n?",
        "",
        result
    )

    result = re.sub(
        r"(?im)^.*ENTRY TIME.*$\n?",
        "",
        result
    )

    return result.strip()


# ============================================================
# FORMAT FINAL RESULT
# ============================================================

def format_result(
    analysis: str,
    signal: str,
    confidence: int,
    entry_time,
    entry_delay: int
):

    if signal == "CALL":
        direction = "🟢 CALL (UP)"
    else:
        direction = "🔴 PUT (DOWN)"

    final_text = f"""
🎯 الإشارة: {direction} {confidence}%

📊 نسبة الثقة: {confidence}%
⏱️ وقت الدخول: {entry_time.strftime("%H:%M")}
⏳ بعد: {entry_delay} دقيقة

{analysis}
""".strip()

    return final_text


# ============================================================
# GEMINI ANALYSIS
# ============================================================

async def analyze_chart(image_bytes: bytes):

    models_to_try = [
        GEMINI_MODEL
    ]

    # Fallback models only if primary fails temporarily.
    fallback_models = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
    ]

    for model in fallback_models:

        if model not in models_to_try:
            models_to_try.append(model)

    last_error = None

    for index, model in enumerate(models_to_try):

        try:

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
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

            confidence = extract_confidence(text)

            entry_delay = extract_entry_delay(text)

            if signal not in ("CALL", "PUT"):
                raise RuntimeError(
                    "Gemini did not return a valid CALL/PUT signal"
                )

            if confidence is None:
                raise RuntimeError(
                    "Gemini did not return a valid confidence"
                )

            if entry_delay is None:
                raise RuntimeError(
                    "Gemini did not return a valid entry delay"
                )

            analysis = clean_result(
                text,
                signal,
                confidence,
                entry_delay
            )

            return {
                "analysis": analysis,
                "signal": signal,
                "confidence": confidence,
                "entry_delay": entry_delay,
                "model": model,
            }

        except Exception as e:

            last_error = e

            error_text = str(e).lower()

            temporary_error = any(
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
                    "quota",
                ]
            )

            # Do not switch models for normal analysis/parsing errors.
            if not temporary_error:
                break

            # Do not unnecessarily wait between models.
            continue

    raise RuntimeError(str(last_error))


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

    status_message = await update.message.reply_text(
        "🔎 جاري تحليل الشارت..."
    )

    try:

        photo = update.message.photo[-1]

        telegram_file = await photo.get_file()

        # Download the largest Telegram image directly.
        # NO RESIZE.
        # NO PIL conversion.
        # NO quality reduction.
        image_bytes = bytes(
            await telegram_file.download_as_bytearray()
        )

        result = await analyze_chart(image_bytes)

        entry_delay = result["entry_delay"]

        entry_time = calculate_entry_time(
            entry_delay
        )

        final_text = format_result(
            result["analysis"],
            result["signal"],
            result["confidence"],
            entry_time,
            entry_delay
        )

        await status_message.edit_text(
            final_text
        )

    except Exception as e:

        await status_message.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{str(e)}"
        )


# ============================================================
# /START
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
        "📸 أرسل Screenshot للشارت.\n"
        "🎯 سأحدد CALL أو PUT.\n"
        "⏱️ وسأحدد وقت الدخول المناسب حسب الشارت.\n\n"
        "الأمر:\n"
        "/help"
    )


# ============================================================
# /HELP
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
        "3️⃣ أرسل الصورة هنا\n\n"
        "البوت يحلل:\n"
        "• Market Structure\n"
        "• Trend\n"
        "• Support / Resistance\n"
        "• Price Action\n"
        "• Confirmation Candle\n"
        "• RSI إذا كان ظاهرًا\n"
        "• EMA 5/13 إذا كانت ظاهرة\n\n"
        "⏱️ البوت لا يفترض M1.\n"
        "يحدد الإطار من الشارت إذا كان ظاهرًا.\n\n"
        "⏳ وقت الدخول يحدده حسب الحالة، "
        "وليس دائمًا بعد دقيقة."
    )


# ============================================================
# /WIN
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

    percentage = (
        wins / total * 100
        if total > 0
        else 0
    )

    await update.message.reply_text(
        f"✅ WIN\n\n"
        f"🏆 Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"📊 Total: {total}\n"
        f"📈 Win Rate: {percentage:.1f}%"
    )


# ============================================================
# /LOSS
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

    percentage = (
        wins / total * 100
        if total > 0
        else 0
    )

    await update.message.reply_text(
        f"❌ LOSS\n\n"
        f"🏆 Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"📊 Total: {total}\n"
        f"📈 Win Rate: {percentage:.1f}%"
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

    total = wins + losses

    percentage = (
        wins / total * 100
        if total > 0
        else 0
    )

    await update.message.reply_text(
        f"📊 إحصائيات ZinoQuotexSignalAI\n\n"
        f"🏆 Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"📊 Total: {total}\n"
        f"📈 Win Rate: {percentage:.1f}%"
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

    # Start Render health server
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
        "ZinoQuotexSignalAI started successfully."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
