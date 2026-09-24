import os
import io
import json
import base64
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import httpx
from aiohttp import web
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from google import genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_RAW = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")

PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID_RAW:
raise RuntimeError("OWNER_ID is missing")

if not GEMINI_MODEL:
raise RuntimeError("GEMINI_MODEL is missing")

if not TWELVE_DATA_API_KEY:
raise RuntimeError("TWELVE_DATA_API_KEY is missing")

try:
OWNER_ID = int(OWNER_ID_RAW)
except ValueError:
raise RuntimeError("OWNER_ID must be an integer")

logging.basicConfig(
format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
level=logging.INFO,
)

logger = logging.getLogger("ZinoQuotexSignalAI")

UTC_MINUS_3 = timezone(timedelta(hours=-3))

FOREX_PAIRS = [
"EUR/USD",
"GBP/USD",
"USD/JPY",
"USD/CHF",
"AUD/USD",
"NZD/USD",
"USD/CAD",
"EUR/GBP",
"EUR/JPY",
"GBP/JPY",
"AUD/JPY",
"EUR/AUD",
]

gemini_client = genai.Client(
api_key=GEMINI_API_KEY
)

def is_owner(update: Update) -> bool:
user = update.effective_user

if not user:
    return False

return user.id == OWNER_ID

def format_price(price: float) -> str:
if price >= 100:
return f"{price:.3f}"

if price >= 10:
    return f"{price:.4f}"

if price >= 1:
    return f"{price:.5f}"

return f"{price:.6f}"

async def get_forex_data(
client: httpx.AsyncClient,
symbol: str,
outputsize: int = 80,
):
url = "https://api.twelvedata.com/time_series"

params = {
    "symbol": symbol,
    "interval": "1min",
    "outputsize": outputsize,
    "apikey": TWELVE_DATA_API_KEY,
    "format": "JSON",
}

try:
    response = await client.get(
        url,
        params=params,
        timeout=15.0,
    )

    response.raise_for_status()

    data = response.json()

except Exception as e:
    logger.error(
        "Twelve Data request failed for %s: %s",
        symbol,
        e,
    )
    return None

if not isinstance(data, dict):
    return None

if data.get("status") == "error":
    logger.error(
        "Twelve Data error for %s: %s",
        symbol,
        data.get("message"),
    )
    return None

values = data.get("values")

if not values:
    logger.error(
        "No data for %s",
        symbol,
    )
    return None

try:
    df = pd.DataFrame(values)

    df["datetime"] = pd.to_datetime(
        df["datetime"]
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    df = df.sort_values(
        "datetime"
    ).reset_index(drop=True)

    if len(df) < 20:
        return None

    return df

except Exception as e:
    logger.exception(
        "Data parsing failed for %s: %s",
        symbol,
        e,
    )
    return None

def make_2m_candles(df: pd.DataFrame):
work = df.copy()

work["datetime"] = pd.to_datetime(
    work["datetime"]
)

work = work.set_index(
    "datetime"
)

result = (
    work.resample("2min")
    .agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }
    )
    .dropna()
    .reset_index()
)

return result

def calculate_atr(
df: pd.DataFrame,
period: int = 10,
):
previous_close = df["close"].shift(1)

tr1 = df["high"] - df["low"]

tr2 = (
    df["high"] - previous_close
).abs()

tr3 = (
    df["low"] - previous_close
).abs()

true_range = pd.concat(
    [
        tr1,
        tr2,
        tr3,
    ],
    axis=1,
).max(axis=1)

return true_range.rolling(
    period
).mean()

def calculate_keltner(
df: pd.DataFrame,
):
middle = (
df["close"]
.ewm(
span=20,
adjust=False,
)
.mean()
)

atr = calculate_atr(
    df,
    10,
)

upper = middle + (
    2.0 * atr
)

lower = middle - (
    2.0 * atr
)

return middle, upper, lower

def calculate_adx(
df: pd.DataFrame,
period: int = 14,
):
high = df["high"]
low = df["low"]
close = df["close"]

up_move = high.diff()
down_move = -low.diff()

plus_dm = pd.Series(
    0.0,
    index=df.index,
)

minus_dm = pd.Series(
    0.0,
    index=df.index,
)

plus_condition = (
    (up_move > down_move)
    & (up_move > 0)
)

minus_condition = (
    (down_move > up_move)
    & (down_move > 0)
)

plus_dm.loc[
    plus_condition
] = up_move.loc[
    plus_condition
]

minus_dm.loc[
    minus_condition
] = down_move.loc[
    minus_condition
]

previous_close = close.shift(1)

tr = pd.concat(
    [
        high - low,
        (
            high - previous_close
        ).abs(),
        (
            low - previous_close
        ).abs(),
    ],
    axis=1,
).max(axis=1)

atr = tr.rolling(
    period
).mean()

plus_di = (
    100
    * plus_dm.rolling(period).mean()
    / atr
)

minus_di = (
    100
    * minus_dm.rolling(period).mean()
    / atr
)

denominator = (
    plus_di + minus_di
)

dx = (
    100
    * (plus_di - minus_di).abs()
    / denominator.replace(0, pd.NA)
)

adx = dx.rolling(
    period
).mean()

return adx, plus_di, minus_di

def calculate_rsi(
df: pd.DataFrame,
period: int = 14,
):
delta = df["close"].diff()

gain = delta.clip(
    lower=0
)

loss = -delta.clip(
    upper=0
)

avg_gain = gain.rolling(
    period
).mean()

avg_loss = loss.rolling(
    period
).mean()

rs = (
    avg_gain
    / avg_loss.replace(
        0,
        pd.NA,
    )
)

return 100 - (
    100 / (1 + rs)
)

def get_market_structure(
df: pd.DataFrame,
):
if len(df) < 8:
return "NEUTRAL"

recent = df.tail(8)

first = recent.iloc[:4]
second = recent.iloc[4:]

first_high = first["high"].max()
second_high = second["high"].max()

first_low = first["low"].min()
second_low = second["low"].min()

if (
    second_high > first_high
    and second_low > first_low
):
    return "BULLISH"

if (
    second_high < first_high
    and second_low < first_low
):
    return "BEARISH"

return "NEUTRAL"

def score_pair(
df: pd.DataFrame,
):
if len(df) < 40:
return -999, "NEUTRAL"

work = df.copy()

middle, upper, lower = calculate_keltner(
    work
)

adx, plus_di, minus_di = calculate_adx(
    work
)

rsi = calculate_rsi(
    work
)

work["middle"] = middle
work["upper"] = upper
work["lower"] = lower
work["adx"] = adx
work["plus_di"] = plus_di
work["minus_di"] = minus_di
work["rsi"] = rsi

last = work.iloc[-1]

score = 0

if last["close"] > last["open"]:
    score += 2
elif last["close"] < last["open"]:
    score -= 2

change = (
    last["close"]
    - work.iloc[-5]["close"]
)

if change > 0:
    score += 2
elif change < 0:
    score -= 2

structure = get_market_structure(
    work
)

if structure == "BULLISH":
    score += 3
elif structure == "BEARISH":
    score -= 3

if pd.notna(last["adx"]):
    if last["adx"] >= 20:
        if last["plus_di"] > last["minus_di"]:
            score += 3
        elif last["minus_di"] > last["plus_di"]:
            score -= 3

if pd.notna(last["middle"]):
    if last["close"] > last["middle"]:
        score += 1
    elif last["close"] < last["middle"]:
        score -= 1

candle_body = abs(
    last["close"]
    - last["open"]
)

candle_range = (
    last["high"]
    - last["low"]
)

if candle_range > 0:
    body_ratio = (
        candle_body
        / candle_range
    )

    if body_ratio >= 0.55:
        if last["close"] > last["open"]:
            score += 2
        else:
            score -= 2

if pd.notna(last["rsi"]):
    if last["rsi"] >= 70:
        score -= 1
    elif last["rsi"] <= 30:
        score += 1

if score >= 0:
    direction = "UP"
else:
    direction = "DOWN"

return abs(score), direction

def create_chart(
df: pd.DataFrame,
symbol: str,
):
data = df.tail(40).copy()

middle, upper, lower = calculate_keltner(
    data
)

fig, ax = plt.subplots(
    figsize=(12, 6)
)

for i, (_, row) in enumerate(
    data.iterrows()
):
    open_price = row["open"]
    close_price = row["close"]
    high_price = row["high"]
    low_price = row["low"]

    if close_price >= open_price:
        candle_color = "green"
    else:
        candle_color = "red"

    ax.plot(
        [i, i],
        [low_price, high_price],
        color=candle_color,
        linewidth=1.2,
    )

    bottom = min(
        open_price,
        close_price,
    )

    height = abs(
        close_price
        - open_price
    )

    if height == 0:
        height = max(
            (
                high_price
                - low_price
            ) * 0.02,
            0.000001,
        )

    ax.bar(
        i,
        height,
        bottom=bottom,
        width=0.65,
        color=candle_color,
        alpha=0.8,
    )

x = range(
    len(data)
)

ax.plot(
    x,
    middle,
    linewidth=1.2,
    label="Keltner Middle",
)

ax.plot(
    x,
    upper,
    linewidth=0.8,
    linestyle="--",
    label="Keltner Upper",
)

ax.plot(
    x,
    lower,
    linewidth=0.8,
    linestyle="--",
    label="Keltner Lower",
)

ax.set_title(
    f"{symbol} - 2M"
)

ax.set_xlabel(
    "Candles"
)

ax.set_ylabel(
    "Price"
)

ax.grid(
    alpha=0.2
)

ax.legend()

plt.tight_layout()

image = io.BytesIO()

plt.savefig(
    image,
    format="png",
    dpi=150,
    bbox_inches="tight",
)

plt.close(fig)

image.seek(0)

return image

ANALYSIS_PROMPT = """
أنت محلل تقني متخصص في التداول قصير المدى على Forex.

حلل صورة الشارت.

ركز بالترتيب على:

Price Action
فتح وإغلاق الشموع
High و Low
Market Structure
Breakout و Failed Breakout
Liquidity Sweep و Rejection
Candle Confirmation
ADX و DMI
Keltner Channel 20/10
RSI كعامل ثانوي

اختر UP أو DOWN فقط.

ممنوع NO SIGNAL.
ممنوع WAIT.
ممنوع إعطاء اتجاهين.

راقب Higher High و Higher Low في الصعود.
راقب Lower High و Lower Low في الهبوط.

لا تعتمد على شمعة واحدة فقط.

حدد دخولًا بعد 1 أو 2 أو 3 دقائق حسب التحليل.

أعط وقت الدخول فقط بدون وقت انتهاء.

أعط سعر الدخول ومستوى الإلغاء.

في UP:
الإلغاء إذا أغلقت شمعة تحت مستوى الإلغاء.

في DOWN:
الإلغاء إذا أغلقت شمعة فوق مستوى الإلغاء.

السعر بحد أقصى 6 أرقام بعد الفاصلة.

أرجع JSON فقط بهذا الشكل:

{
"direction": "UP",
"confidence": 75,
"entry_delay_minutes": 2,
"entry_price": 1.12345,
"cancellation_price": 1.12280,
"reason": "سبب مختصر",
"structure": "BULLISH",
"momentum": "BULLISH"
}
"""

async def analyze_with_gemini(
image_bytes: bytes,
symbol: str,
):
prompt = (
ANALYSIS_PROMPT
+ "\n\nالزوج: "
+ symbol
)

try:
    response = await asyncio.to_thread(
        gemini_client.models.generate_content,
        model=GEMINI_MODEL,
        contents=[
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt
                    },
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(
                                image_bytes
                            ).decode("utf-8"),
                        }
                    },
                ],
            }
        ],
    )

    text = getattr(
        response,
        "text",
        None,
    )

    if not text:
        raise RuntimeError(
            "Gemini returned empty response"
        )

    text = text.strip()

    if text.startswith("```"):
        text = text.replace(
            "```json",
            "",
            1,
        )

        text = text.replace(
            "```",
            "",
        ).strip()

    try:
        result = json.loads(
            text
        )
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start < 0 or end <= start:
            raise RuntimeError(
                "Gemini did not return valid JSON"
            )

        result = json.loads(
            text[start:end + 1]
        )

    direction = str(
        result.get(
            "direction",
            "",
        )
    ).upper()

    if direction not in [
        "UP",
        "DOWN",
    ]:
        raise RuntimeError(
            "Gemini returned invalid direction"
        )

    confidence = int(
        result.get(
            "confidence",
            50,
        )
    )

    confidence = max(
        50,
        min(
            confidence,
            95,
        ),
    )

    delay = int(
        result.get(
            "entry_delay_minutes",
            1,
        )
    )

    if delay not in [1, 2, 3]:
        delay = 1

    entry_price = float(
        result.get(
            "entry_price",
            0,
        )
    )

    cancellation_price = float(
        result.get(
            "cancellation_price",
            0,
        )
    )

    if entry_price <= 0:
        raise RuntimeError(
            "Gemini returned invalid entry price"
        )

    if cancellation_price <= 0:
        raise RuntimeError(
            "Gemini returned invalid cancellation price"
        )

    return {
        "direction": direction,
        "confidence": confidence,
        "entry_delay_minutes": delay,
        "entry_price": entry_price,
        "cancellation_price": cancellation_price,
        "reason": str(
            result.get(
                "reason",
                "Price Action confirmation.",
            )
        ),
        "structure": str(
            result.get(
                "structure",
                "NEUTRAL",
            )
        ).upper(),
        "momentum": str(
            result.get(
                "momentum",
                "NEUTRAL",
            )
        ).upper(),
    }

except Exception as e:
    logger.exception(
        "Gemini analysis failed for %s",
        symbol,
    )
    raise RuntimeError(
        f"Gemini analysis failed: {e}"
    )

def entry_time(
delay: int,
):
now = datetime.now(
UTC_MINUS_3
)

value = now + timedelta(
    minutes=delay
)

return value.strftime(
    "%H:%M"
)

async def find_best_pair():

logger.info(
    "Starting pair scan"
)

timeout = httpx.Timeout(
    20.0,
    connect=10.0,
)

async with httpx.AsyncClient(
    timeout=timeout
) as client:

    tasks = [
        get_forex_data(
            client,
            symbol,
            80,
        )
        for symbol in FOREX_PAIRS
    ]

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

candidates = []

for symbol, data in zip(
    FOREX_PAIRS,
    results,
):

    if isinstance(
        data,
        Exception,
    ):
        logger.error(
            "Error for %s: %s",
            symbol,
            data,
        )
        continue

    if data is None:
        continue

    try:
        candles = make_2m_candles(
            data
        )

        score, direction = score_pair(
            candles
        )

        candidates.append(
            {
                "symbol": symbol,
                "score": score,
                "direction": direction,
                "data": candles,
            }
        )

        logger.info(
            "%s score=%s direction=%s",
            symbol,
            score,
            direction,
        )

    except Exception as e:
        logger.exception(
            "Scoring error for %s: %s",
            symbol,
            e,
        )

if not candidates:
    raise RuntimeError(
        "No valid forex data available"
    )

candidates.sort(
    key=lambda item: item["score"],
    reverse=True,
)

best = candidates[0]

logger.info(
    "Best pair: %s score=%s direction=%s",
    best["symbol"],
    best["score"],
    best["direction"],
)

return best

def format_signal(
symbol: str,
result: dict,
):
direction = result["direction"]

if direction == "UP":
    direction_text = "🟢 UP — شراء"

    cancel_text = (
        "إلغاء إذا أغلقت الشمعة تحت "
        + format_price(
            result["cancellation_price"]
        )
    )

else:
    direction_text = "🔴 DOWN — بيع"

    cancel_text = (
        "إلغاء إذا أغلقت الشمعة فوق "
        + format_price(
            result["cancellation_price"]
        )
    )

return (
    "🎓 تحليل زينو\n\n"
    + f"🎯 Confidence: {result['confidence']}%\n"
    + f"📊 {symbol} · ⏱ 2M\n"
    + "━━━━━━━━━━━━━━\n\n"
    + f"🎯 القرار: {direction_text}\n"
    + f"🕐 وقت الدخول: {entry_time(result['entry_delay_minutes'])}\n"
    + f"⏳ بعد: {result['entry_delay_minutes']} دقيقة\n\n"
    + f"💵 سعر الدخول: {format_price(result['entry_price'])}\n"
    + f"🛑 {cancel_text}\n\n"
    + f"📈 Market Structure: {result['structure']}\n"
    + f"⚡ Momentum: {result['momentum']}\n\n"
    + f"📝 السبب: {result['reason']}"
)

async def start(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
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
    "🎓 ZinoQuotexSignalAI",
    reply_markup=InlineKeyboardMarkup(
        keyboard
    ),
)

async def get_signal(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):
if not is_owner(update):
return

query = update.callback_query

await query.answer()

message = await query.message.reply_text(
    "🔎 جاري فحص الأزواج..."
)

try:

    best = await find_best_pair()

    symbol = best["symbol"]
    candles = best["data"]

    await message.edit_text(
        f"📊 {symbol}\n"
        "🔎 تم اختيار أفضل زوج بعد فحص الأزواج.\n"
        "📈 جاري إنشاء الشارت..."
    )

    chart = await asyncio.to_thread(
        create_chart,
        candles,
        symbol,
    )

    if chart is None:
        raise RuntimeError(
            "Chart creation failed"
        )

    chart_bytes = chart.getvalue()

    if not chart_bytes:
        raise RuntimeError(
            "Chart is empty"
        )

    logger.info(
        "Chart created for %s",
        symbol,
    )

    await message.edit_text(
        f"📊 {symbol}\n"
        "🧠 جاري تحليل الشارت..."
    )

    result = await analyze_with_gemini(
        chart_bytes,
        symbol,
    )

    text = format_signal(
        symbol,
        result,
    )

    await message.delete()

    await query.message.reply_photo(
        photo=io.BytesIO(
            chart_bytes
        ),
        caption=text,
    )

except Exception as e:

    logger.exception(
        "Get Signal failed"
    )

    error = (
        "❌ حدث خطأ أثناء التحليل:\n\n"
        + str(e)[:1000]
    )

    try:
        await message.edit_text(
            error
        )
    except Exception:
        await query.message.reply_text(
            error
        )

async def handle_text(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):
if not is_owner(update):
return

await update.message.reply_text(
    "اضغط 🎯 Get Signal للحصول على الإشارة."
)

async def handle_photo(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):
if not is_owner(update):
return

await update.message.reply_text(
    "🎯 استخدم Get Signal ليقوم البوت بفحص الأزواج وتحليل الشارت تلقائيًا."
)

async def health(
request,
):
return web.Response(
text="ZinoQuotexSignalAI is running"
)

async def start_health_server():

app = web.Application()

app.router.add_get(
    "/",
    health,
)

app.router.add_get(
    "/health",
    health,
)

runner = web.AppRunner(
    app
)

await runner.setup()

site = web.TCPSite(
    runner,
    "0.0.0.0",
    PORT,
)

await site.start()

logger.info(
    "HTTP server started on port %s",
    PORT,
)

return runner

async def main():

application = (
    Application.builder()
    .token(BOT_TOKEN)
    .build()
)

application.add_handler(
    CommandHandler(
        "start",
        start,
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
        filters.TEXT
        & ~filters.COMMAND,
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

if name == "main":

try:
    asyncio.run(
        main()
    )
except KeyboardInterrupt:
    logger.info(
        "Bot stopped"
)
