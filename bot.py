import os
import io
import json
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_TEXT = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

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

client = genai.Client(api_key=GEMINI_API_KEY)

stats = {
"total": 0,
"wins": 0,
"losses": 0
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

UP = CALL
DOWN = PUT

اعتمد على المؤشرين فقط:

Keltner Channel 20/10
ADX 14/14

لا تستخدم أي مؤشر آخر.

لا تستخدم RSI.
لا تستخدم MACD.
لا تستخدم Moving Average.
لا تستخدم Parabolic SAR.
لا تستخدم Stochastic.
لا تستخدم Bollinger Bands.
لا تستخدم أي مؤشر إضافي.

حلل حركة السعر والشموع الظاهرة في الصورة مع المؤشرين الأساسيين.

ركز على:

اتجاه السعر.
القمم والقيعان.
Higher High و Higher Low.
Lower High و Lower Low.
افتتاح الشموع.
إغلاق الشموع.
قوة جسم الشمعة.
الذيول.
الاختراقات.
إغلاق الاختراق.
الزخم.
الشمعة الحالية.
الشمعة السابقة.

Keltner Channel 20/10:

استخدم Keltner لتحديد اتجاه الحركة، ميل القناة، مكان السعر داخل القناة، والاختراقات.

ADX 14/14:

استخدم ADX لمعرفة قوة الاتجاه.
اقرأ +DI و -DI لمعرفة الطرف المسيطر.
ADX وحده لا يحدد الاتجاه.

UP عندما تكون حركة السعر وKeltner وADX وDI متوافقة في الاتجاه الصاعد.

DOWN عندما تكون حركة السعر وKeltner وADX وDI متوافقة في الاتجاه الهابط.

لا تجعل كل الإشارات UP.
لا تجعل كل الإشارات DOWN.

إذا كان الاختراق مجرد ذيل فلا تعتبره اختراقاً مؤكداً.
أعط أهمية لإغلاق الشمعة.

وقت الدخول:

الدخول يكون في بداية الشمعة القادمة مباشرة.

إذا كان M1:
الدخول في بداية الدقيقة التالية.

إذا كان M2:
الدخول في بداية الشمعة التالية ذات الدقيقتين.

اعتمد وقت الشارت الظاهر في الصورة.

منطقة وقت Quotex هي UTC-3.

أعطني وقت الدخول فقط.

لا تعطِ وقت انتهاء.

سعر الدخول:
استخدم السعر الظاهر في الشارت إذا كان واضحاً.

شرط إلغاء الإشارة:
يجب أن يكون مرتبطاً بالبنية السعرية الحالية.

UP:
تلغى الإشارة إذا أغلقت الشمعة الحالية تحت مستوى إبطال الاتجاه الصاعد قبل الدخول.

DOWN:
تلغى الإشارة إذا أغلقت الشمعة الحالية فوق مستوى إبطال الاتجاه الهابط قبل الدخول.

لا تخترع مستوى عشوائياً.

نسبة الثقة تعتمد على توافق:

حركة السعر
Keltner 20/10
ADX 14/14
+DI / -DI
الشموع
الزخم

لا تضع نسبة عشوائية.

أخرج JSON فقط.

لا تستخدم Markdown.
لا تستخدم ```json.
لا تكتب أي كلام خارج JSON.

JSON يجب أن يحتوي فقط على:

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

direction:
UP أو DOWN

confidence:
رقم من 0 إلى 100.

لا تضف حقولاً أخرى.
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

confidence = int(data["confidence"])

if confidence < 0:
    confidence = 0

if confidence > 100:
    confidence = 100

if direction == "UP":
    decision = "🟢 UP — شراء (Call)"
else:
    decision = "🔴 DOWN — بيع (Put)"

return (
    "🎓 تحليل زينو\n"
    "✅ Solid Setup · " + str(confidence) + "%\n"
    "📊 " + data["asset"] + " · ⏱ " + data["timeframe"] + "\n"
    "━━━━━━━━━━━━━━━\n"
    "🎯 القرار: " + decision + "\n"
    "🕐 وقت الشارت: " + data["chart_time"] + "\n"
    "⏰ وقت الدخول: " + data["entry_time"] + "\n"
    "⏳ مدة الصفقة: " + data["timeframe"] + "\n"
    "💵 سعر الدخول: " + data["entry_price"] + "\n"
    "🛑 شرط إلغاء الإشارة: " + data["cancellation_condition"] + "\n"
    "━━━━━━━━━━━━━━━\n"
    "📐 الاتجاه: " + data["trend"] + "\n"
    "📊 Keltner 20/10: " + data["keltner"] + "\n"
    "📊 ADX 14/14: " + data["adx"] + "\n"
    "🕯 النموذج: " + data["candle"] + "\n"
    "📈 الزخم: " + data["momentum"] + "\n"
    "━━━━━━━━━━━━━━━\n"
    "🧠 شرح زينو:\n"
    + data["reason"]
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

if total:
    winrate = wins / total * 100
else:
    winrate = 0

await update.message.reply_text(
    "📊 إحصائيات زينو\n"
    "━━━━━━━━━━━━━━━\n"
    "الصفقات: " + str(total) + "\n"
    "WIN: " + str(wins) + "\n"
    "LOSS: " + str(losses) + "\n"
    "Win Rate: " + f"{winrate:.1f}%"
)

async def reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

if update.effective_user.id != OWNER_ID:
    return

stats["total"] = 0
stats["wins"] = 0
stats["losses"] = 0

await update.message.reply_text(
    "تم تصفير الإحصائيات."
)

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

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

    await telegram_file.download_to_memory(buffer)

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
        "❌ حدث خطأ أثناء التحليل:\n\n" + error
    )

async def result_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

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

if total:
    winrate = wins / total * 100
else:
    winrate = 0

await query.edit_message_reply_markup(
    reply_markup=None
)

await query.message.reply_text(
    message + "\n\n"
    "📊 الصفقات: " + str(total) + "\n"
    "✅ WIN: " + str(wins) + "\n"
    "❌ LOSS: " + str(losses) + "\n"
    "🎯 Win Rate: " + f"{winrate:.1f}%"
)

async def error_handler(update, context):

print(
    "Telegram error:",
    context.error
)

def main():

print("ZinoQuotexSignalAI starting...")
print("Keltner Channel 20/10")
print("ADX 14/14")
print("Gemini model:", GEMINI_MODEL)

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
