import os
import json
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# ============================================================
# ENV
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

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

client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# SIMPLE HEALTH SERVER FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ZinoQuotexSignalAI is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


threading.Thread(
    target=start_health_server,
    daemon=True
).start()


# ============================================================
# TRADE STATS
# ============================================================

stats = {
    "WIN": 0,
    "LOSS": 0,
}


# ============================================================
# OWNER CHECK
# ============================================================

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ هذا البوت خاص.")
        return

    await update.message.reply_text(
        "🎓 Zino Signal AI\n\n"
        "📸 أرسل صورة الشارت وسأحللها مباشرة."
    )


# ============================================================
# STATS
# ============================================================

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user or not is_owner(update.effective_user.id):
        return

    total = stats["WIN"] + stats["LOSS"]

    if total == 0:
        rate = 0
    else:
        rate = (stats["WIN"] / total) * 100

    await update.message.reply_text(
        "📊 إحصائيات الصفقات\n"
        "━━━━━━━━━━━━━━\n\n"
        f"✅ WIN: {stats['WIN']}\n"
        f"❌ LOSS: {stats['LOSS']}\n"
        f"📌 Total: {total}\n"
        f"🎯 Win Rate: {rate:.1f}%"
    )


# ============================================================
# RESET STATS
# ============================================================

async def reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user or not is_owner(update.effective_user.id):
        return

    stats["WIN"] = 0
    stats["LOSS"] = 0

    await update.message.reply_text(
        "♻️ تم تصفير إحصائيات الصفقات."
    )


# ============================================================
# BUTTONS
# ============================================================

async def result_button(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    if not query.from_user or not is_owner(query.from_user.id):
        return

    data = query.data

    if data == "WIN":
        stats["WIN"] += 1
        text = "✅ تم تسجيل WIN"

    elif data == "LOSS":
        stats["LOSS"] += 1
        text = "❌ تم تسجيل LOSS"

    else:
        return

    total = stats["WIN"] + stats["LOSS"]
    rate = (stats["WIN"] / total * 100) if total else 0

    await query.edit_message_reply_markup(reply_markup=None)

    await query.message.reply_text(
        f"{text}\n\n"
        f"📊 WIN: {stats['WIN']} | LOSS: {stats['LOSS']}\n"
        f"🎯 Win Rate: {rate:.1f}%"
    )


# ============================================================
# GEMINI ANALYSIS
# ============================================================

async def analyze_chart(image: Image.Image):

    prompt = r"""
You are Zino Signal AI, a fast and strict Quotex chart analyzer.

Analyze ONLY the chart image provided.

The chart may be 1M, 2M, 3M, 5M or another timeframe.
Identify the actual timeframe visible in the chart when possible.

IMPORTANT:
- Do NOT invent market data.
- Do NOT invent an entry price.
- Read the current visible price from the chart.
- If the exact current price is visible, return it as a STRING exactly as displayed.
- Preserve all visible digits.
- If the exact price cannot be read, return "UNKNOWN".
- Do not fabricate RSI, MACD, Bollinger Bands or momentum values.
- If an indicator is not visible or cannot reasonably be calculated from the image, return "N/A".
- Confidence and strength are your analysis estimates, not guaranteed probabilities.
- Do not always choose UP.
- UP and DOWN must both be considered.
- Analyze the actual price action before deciding.

MAIN ANALYSIS PRIORITIES:

1. Candle structure
   - Open
   - Close
   - High
   - Low
   - Candle body
   - Wick/rejection
   - Strong bullish/bearish candles

2. Market structure
   - Higher highs / higher lows
   - Lower highs / lower lows
   - Consolidation
   - Breakout
   - Failed breakout

3. Momentum
   - Current candle strength
   - Consecutive candles
   - Acceleration/deceleration
   - Bullish/bearish pressure

4. Price action
   - Engulfing
   - Hammer
   - Pin bar
   - Rejection
   - Break and retest
   - Liquidity sweep when clearly visible

5. Indicators
   Only use indicators that are actually visible:
   - RSI
   - MACD histogram
   - Bollinger Bands
   - Trend
   - Momentum

6. Entry
   The entry price must be the current visible market price.
   The user wants the entry at the beginning of the next candle.
   Return entry timing as a small practical number of minutes.

DO NOT use "NO SIGNAL".

Always make a directional decision:
UP or DOWN.

However, confidence must reflect the quality of the setup.
Do not give artificially high confidence.

Return ONLY valid JSON.
No markdown.
No explanation outside JSON.

Use exactly this JSON structure:

{
  "asset": "GBP/USD",
  "timeframe": "2M",
  "direction": "UP",
  "confidence": 84,
  "strength": 69,
  "entry_price": "1.3516",
  "entry_delay_minutes": 1,
  "technical": "bullish",
  "rsi": "54",
  "trend": "bullish",
  "volatility": "0.009",
  "macd_hist": "0.0000",
  "bb_position": "0.41",
  "momentum": "0.018"
}

RULES:

asset:
- Read the pair/asset from the chart.
- If unreadable, use "UNKNOWN".

timeframe:
- Read the timeframe from the chart.
- If unreadable, use "UNKNOWN".

direction:
- Only "UP" or "DOWN".

confidence:
- Integer 50 to 95.
- Based on actual chart evidence.

strength:
- Integer 1 to 100.
- Measures how strong the current directional setup is.

entry_price:
- String.
- Exact visible current price.
- Never invent.
- Use "UNKNOWN" if unreadable.

entry_delay_minutes:
- Integer from 0 to 5.
- 0 means enter at the current/next candle opening if appropriate.
- Prefer 1 or 2 when the setup needs the next candle confirmation.
- Never return text.
- Never return "after the minute".
- Never return decimals.

technical:
- "bullish", "bearish", or "neutral".

rsi:
- Visible RSI value as string.
- Otherwise "N/A".

trend:
- "bullish", "bearish", or "neutral".

volatility:
- Value only if visible/calculable.
- Otherwise "N/A".

macd_hist:
- Visible/calculable value.
- Otherwise "N/A".

bb_position:
- Value from 0 to 1 only when Bollinger Bands are visible and usable.
- Otherwise "N/A".

momentum:
- Value only if visible/calculable.
- Otherwise "N/A".

Again:
Return ONLY JSON.
"""


    # Keep original resolution; no artificial resize.
    image = image.convert("RGB")

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(
                data=_image_to_bytes(image),
                mime_type="image/jpeg",
            ),
            prompt,
        ],
        config=types.GenerateContentConfig(
            temperature=0.15,
            max_output_tokens=500,
            response_mime_type="application/json",
        ),
    )

    text = response.text.strip()

    # Remove accidental markdown fences if model adds them.
    if text.startswith("```"):
        text = text.replace("```json", "", 1)
        text = text.replace("```", "", 1)
        text = text.strip()

    data = json.loads(text)

    return data


# ============================================================
# IMAGE BYTES
# ============================================================

def _image_to_bytes(image: Image.Image) -> bytes:
    import io

    buffer = io.BytesIO()

    image.save(
        buffer,
        format="JPEG",
        quality=92,
        optimize=False,
    )

    return buffer.getvalue()


# ============================================================
# FORMAT RESULT
# ============================================================

def format_signal(data: dict) -> str:

    asset = str(data.get("asset", "UNKNOWN"))
    timeframe = str(data.get("timeframe", "UNKNOWN"))

    direction = str(
        data.get("direction", "UP")
    ).upper()

    confidence = data.get("confidence", 0)
    strength = data.get("strength", 0)

    entry_price = str(
        data.get("entry_price", "UNKNOWN")
    )

    delay = data.get(
        "entry_delay_minutes",
        1
    )

    technical = str(
        data.get("technical", "N/A")
    )

    rsi = str(
        data.get("rsi", "N/A")
    )

    trend = str(
        data.get("trend", "N/A")
    )

    volatility = str(
        data.get("volatility", "N/A")
    )

    macd_hist = str(
        data.get("macd_hist", "N/A")
    )

    bb_position = str(
        data.get("bb_position", "N/A")
    )

    momentum = str(
        data.get("momentum", "N/A")
    )

    if direction == "UP":
        emoji = "🟢"
        word = "UP"
    else:
        emoji = "🔴"
        word = "DOWN"

    try:
        delay_int = int(delay)
    except:
        delay_int = 1

    if delay_int < 0:
        delay_int = 0

    if delay_int > 5:
        delay_int = 5

    if delay_int == 0:
        entry_text = "بداية الشمعة القادمة"
    elif delay_int == 1:
        entry_text = "بعد 1 دقيقة"
    else:
        entry_text = f"بعد {delay_int} دقائق"

    return (
        "🎓 تحليل زينو\n\n"
        f"🎯 القرار: {emoji} {word}\n"
        f"📊 Confidence: {confidence}%\n"
        f"💪 Strength: {strength}%\n"
        "━━━━━━━━━━━━━━\n\n"
        f"📊 {asset} · ⏱ {timeframe}\n\n"
        f"💰 سعر الدخول: {entry_price}\n"
        f"🕐 وقت الدخول: {entry_text}\n"
        "━━━━━━━━━━━━━━\n\n"
        f"📈 Technical: {technical}\n"
        f"📊 RSI: {rsi}\n"
        f"📊 Trend: {trend}\n"
        f"📊 Volatility: {volatility}\n"
        f"📊 MACD Hist: {macd_hist}\n"
        f"📊 BB Position: {bb_position}\n"
        f"📊 Momentum: {momentum}"
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ هذا البوت خاص بصاحبه فقط."
        )
        return

    if not update.message or not update.message.photo:
        return

    status = await update.message.reply_text(
        "🔎 تحليل الشارت..."
    )

    try:

        photo = update.message.photo[-1]

        file = await context.bot.get_file(
            photo.file_id
        )

        image_bytes = await file.download_as_bytearray()

        import io

        image = Image.open(
            io.BytesIO(image_bytes)
        )

        result = await analyze_chart(image)

        message = format_signal(result)

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ WIN",
                        callback_data="WIN"
                    ),
                    InlineKeyboardButton(
                        "❌ LOSS",
                        callback_data="LOSS"
                    ),
                ]
            ]
        )

        await status.edit_text(
            message,
            reply_markup=keyboard
        )

    except json.JSONDecodeError:

        await status.edit_text(
            "❌ Gemini أعاد نتيجة غير صالحة.\n"
            "أعد إرسال صورة الشارت."
        )

    except Exception as e:

        error_text = str(e)

        # Avoid huge Telegram error messages.
        if len(error_text) > 700:
            error_text = error_text[:700]

        await status.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{error_text}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

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
            "stats",
            show_stats
        )
    )

    application.add_handler(
        CommandHandler(
            "reset",
            reset_stats
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            result_button
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
