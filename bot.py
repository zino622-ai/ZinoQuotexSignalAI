import os
import io
import json
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
ApplicationBuilder,
CommandHandler,
MessageHandler,
CallbackQueryHandler,
ContextTypes,
filters,
)

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

client = genai.Client(api_key=GEMINI_API_KEY)

stats = {
"total": 0,
"wins": 0,
"losses": 0,
}

class HealthHandler(BaseHTTPRequestHandler):
def do_GET(self):
self.send_response(200)
self.send_header("Content-Type", "text/plain")
self.end_headers()
self.wfile.write(b"ZinoQuotexSignalAI is running")

def log_message(self, format, *args):
    pass

def start_health_server():
port = int(os.getenv("PORT", "10000"))
server = HTTPServer(("0.0.0.0", port), HealthHandler)
server.serve_forever()

threading.Thread(
target=start_health_server,
daemon=True
).start()

PROMPT = """
أنت Zino، محلل شارت Quotex.

حلل صورة الشارت وأعطِ إشارة واحدة فقط:
UP (CALL)
أو
DOWN (PUT)

اعتمد على المؤشرين التاليين فقط:

1. Keltner Channel 20/10
2. ADX 14/14

لا تستخدم أي مؤشر آخر.

لا تستخدم:
RSI
MACD
Moving Average
Parabolic SAR
Stochastic
Bollinger Bands
أو أي مؤشر إضافي.

حلل أيضاً حركة السعر والشموع الظاهرة في الشارت، لأن هذه ليست مؤشرات إضافية.

ركز على:

- اتجاه السعر.
- القمم والقيعان.
- إغلاق الشموع.
- افتتاح الشموع.
- قوة جسم الشمعة.
- الذيول.
- الاختراقات.
- تأكيد الاختراق.
- الزخم.
- الشمعة الحالية.
- الشمعة السابقة.
- توافق السعر مع Keltner.
- قوة الاتجاه مع ADX.
- +DI و -DI الظاهرين مع ADX.

Keltner Channel 20/10:
استخدمه لتحديد اتجاه الحركة ومكان السعر داخل القناة والاختراقات.

ADX 14/14:
استخدمه لمعرفة قوة الاتجاه وقراءة +DI و -DI لتحديد الطرف المسيطر.

لا تجعل ADX وحده يحدد الاتجاه.

UP عندما تكون الأدلة الصاعدة متوافقة.
DOWN عندما تكون الأدلة الهابطة متوافقة.

لا تعطِ UP دائماً.
لا تعطِ DOWN دائماً.

إذا كان هناك اختراق، لا تعتبر مجرد ذيل اختراقاً مؤكداً.
أعطِ أهمية لإغلاق الشمعة وتوافق الزخم وKeltner وADX.

وقت الدخول:
الدخول يكون في بداية الشمعة القادمة مباشرة.

إذا كان الشارت M1، وقت الدخول هو بداية الدقيقة التالية.

إذا كان الشارت M2، وقت الدخول هو بداية الشمعة التالية ذات الدقيقتين.

لا تعطِ وقت دخول بعيداً عن الشمعة القادمة.

اعتمد وقت الشارت الظاهر في الصورة.
منطقة وقت Quotex هي UTC-3.

أعطني وقت الدخول فقط.
لا تعطِ وقت انتهاء.

سعر الدخول:
استخدم السعر الظاهر في الشارت إذا كان واضحاً.

شرط إلغاء الإشارة:
حدد شرطاً واضحاً مبنياً على السعر والبنية الحالية.
لا تخترع مستوى عشوائياً.

مثال UP:
إذا أغلقت الشمعة الحالية تحت المستوى الذي يؤكد بقاء الاتجاه الصاعد قبل الدخول، تلغى الإشارة.

مثال DOWN:
إذا أغلقت الشمعة الحالية فوق المستوى الذي يؤكد بقاء الاتجاه الهابط قبل الدخول، تلغى الإشارة.

نسبة الثقة:
احسبها من توافق Keltner + ADX + حركة السعر + الشموع.
لا تضع نسبة عشوائية.

أخرج JSON فقط.
لا تكتب Markdown.
لا تستخدم ```json.
لا تضف أي كلام خارج JSON.

يجب أن يحتوي JSON على:

direction
confidence
asset
timeframe
chart_time
entry_time
entry_price
cancellation_condition
trend
keltner
adx
candle
momentum
reason

direction يجب أن يكون:
UP
أو
DOWN

confidence رقم من 0 إلى 100.

لا تضف أي حقول أخرى.
"""

SCHEMA = {
"type": "OBJECT",
"properties": {
"direction": {
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
"cancellation_condition": {
"type": "STRING"
},
"trend": {
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
"momentum": {
"type": "STRING"
},
"reason": {
"type": "STRING"
}
},
"required": [
"direction",
"confidence",
"asset",
"timeframe",
"chart_time",
"entry_time",
"entry_price",
"cancellation_condition",
"trend",
"keltner",
"adx",
"candle",
"momentum",
"reason"
]
}

def analyze_image(image_bytes, mime_type):
image = types.Part.from_bytes(
data=image_bytes,
mime_type=mime_type
)

response = client.models.generate_content(
    model=GEMINI_MODEL,
    contents=[
        image,
        PROMPT
    ],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=SCHEMA,
        temperature=0.1,
        max_output_tokens=900
    )
)

if not response.text:
    raise ValueError("Gemini returned an empty response")

try:
    return json.loads(response.text)
except json.JSONDecodeError:
    raise ValueError("Gemini returned invalid JSON")

def format_signal(data):
direction = data["direction"]
confidence = max(0, min(100, int(data["confidence"])))

if direction == "UP":
    decision = "🟢 UP — شراء (Call)"
else:
    decision = "🔴 DOWN — بيع (Put)"

return (
    f"🎓 تحليل زينو\n"
    f"✅ Solid Setup · {confidence}%\n"
    f"📊 {data['asset']} · ⏱ {data['timeframe']}\n"
    f"━━━━━━━━━━━━━━━\n"
    f"🎯 القرار: {decision}\n"
    f"🕐 وقت الشارت: {data['chart_time']}\n"
    f"⏰ وقت الدخول: {data['entry_time']}\n"
    f"⏳ مدة الصفقة: {data['timeframe']}\n"
    f"💵 سعر الدخول: {data['entry_price']}\n"
    f"🛑 شرط إلغاء الإشارة: {data['cancellation_condition']}\n"
    f"━━━━━━━━━━━━━━━\n"
    f"📐 الاتجاه: {data['trend']}\n"
    f"📊 Keltner 20/10: {data['keltner']}\n"
    f"📊 ADX 14/14: {data['adx']}\n"
    f"🕯 النموذج: {data['candle']}\n"
    f"📈 الزخم: {data['momentum']}\n"
    f"━━━━━━━━━━━━━━━\n"
    f"🧠 شرح زينو:\n"
    f"{data['reason']}"
)

def result_buttons():
return InlineKeyboardMarkup([
[
InlineKeyboardButton(
"✅ WIN",
callback_data="win"
),
InlineKeyboardButton(
"❌ LOSS",
callback_data="loss"
)
]
])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != OWNER_ID:
return

await update.message.reply_text(
    "Zino جاهز.\n\n"
    "Keltner Channel 20/10\n"
    "ADX 14/14\n\n"
    "أرسل Screenshot للشارت."
)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
if update.effective_user.id != OWNER_ID:
return

total = stats["total"]
wins = stats["wins"]
losses = stats["losses"]

winrate = (wins / total * 100) if total else 0

await update.message.reply_text(
    f"📊 إحصائيات زينو\n"
    f"الصفقات: {total}\n"
    f"WIN: {wins}\n"
    f"LOSS: {losses}\n"
    f"Win Rate: {winrate:.1f}%"
)

async def photo_handler(
update: Update,
context: ContextTypes.DEFAULT_TYPE
):
if update.effective_user.id != OWNER_ID:
return

message = update.message

status = await message.reply_text(
    "🔎 جاري تحليل الشارت..."
)

try:
    photo = message.photo[-1]
    telegram_file = await photo.get_file()

    buffer = io.BytesIO()

    await telegram_file.download_to_memory(
        buffer
    )

    image_bytes = buffer.getvalue()

    if not image_bytes:
        raise ValueError("الصورة فارغة")

    mime_type = "image/jpeg"

    if image_bytes.startswith(b"\x89PNG"):
        mime_type = "image/png"
    elif image_bytes.startswith(b"RIFF"):
        mime_type = "image/webp"

    data = await asyncio.to_thread(
        analyze_image,
        image_bytes,
        mime_type
    )

    result = format_signal(data)

    await status.edit_text(
        result,
        reply_markup=result_buttons()
    )

except Exception as e:
    error = str(e)

    if len(error) > 700:
        error = error[:700]

    await status.edit_text(
        "❌ حدث خطأ أثناء التحليل:\n\n"
        + error
    )

async def result_handler(
update: Update,
context: ContextTypes.DEFAULT_TYPE
):
query = update.callback_query

if query.from_user.id != OWNER_ID:
    await query.answer()
    return

await query.answer()

if query.data == "win":
    stats["wins"] += 1
    stats["total"] += 1
    message = "✅ تم تسجيل WIN"

elif query.data == "loss":
    stats["losses"] += 1
    stats["total"] += 1
    message = "❌ تم تسجيل LOSS"

else:
    return

total = stats["total"]
wins = stats["wins"]
losses = stats["losses"]

winrate = wins / total * 100 if total else 0

await query.edit_message_reply_markup(
    reply_markup=None
)

await query.message.reply_text(
    f"{message}\n\n"
    f"📊 الصفقات: {total}\n"
    f"✅ WIN: {wins}\n"
    f"❌ LOSS: {losses}\n"
    f"🎯 Win Rate: {winrate:.1f}%"
)

async def reset_stats(
update: Update,
context: ContextTypes.DEFAULT_TYPE
):
if update.effective_user.id != OWNER_ID:
return

stats["total"] = 0
stats["wins"] = 0
stats["losses"] = 0

await update.message.reply_text(
    "تم تصفير الإحصائيات."
)

async def error_handler(
update: object,
context: ContextTypes.DEFAULT_TYPE
):
print("Telegram error:", context.error)

def main():
print("ZinoQuotexSignalAI starting...")
print("Keltner Channel 20/10")
print("ADX 14/14")
print(f"Gemini model: {GEMINI_MODEL}")

app = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .build()
)

app.add_handler(
    CommandHandler("start", start)
)

app.add_handler(
    CommandHandler("stats", stats_command)
)

app.add_handler(
    CommandHandler("resetstats", reset_stats)
)

app.add_handler(
    MessageHandler(
        filters.PHOTO,
        photo_handler
    )
)

app.add_handler(
    CallbackQueryHandler(
        result_handler,
        pattern="^(win|loss)$"
    )
)

app.add_error_handler(
    error_handler
)

app.run_polling(
    allowed_updates=Update.ALL_TYPES,
    drop_pending_updates=True
)

if name == "main":
main()
