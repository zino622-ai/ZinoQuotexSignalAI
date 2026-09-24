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
from telegram import (
Update,
InlineKeyboardButton,
InlineKeyboardMarkup,
)
from telegram.ext import (
Application,
CommandHandler,
CallbackQueryHandler,
ContextTypes,
MessageHandler,
filters,
)

============================================================

CONFIG

============================================================

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

============================================================

LOGGING

============================================================

logging.basicConfig(
format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
level=logging.INFO,
)

logger = logging.getLogger("ZinoQuotexSignalAI")

============================================================

TIMEZONE

============================================================

UTC_MINUS_3 = timezone(timedelta(hours=-3))

============================================================

FOREX PAIRS

============================================================

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

============================================================

GEMINI CLIENT

============================================================

gemini_client = genai.Client(
api_key=GEMINI_API_KEY
)

============================================================

OWNER CHECK

============================================================

def is_owner(update: Update) -> bool:
user = update.effective_user

if not user:
    return False

return user.id == OWNER_ID

============================================================

PRICE FORMAT

============================================================

def format_price(price: float) -> str:
if price >= 100:
decimals = 3
elif price >= 10:
decimals = 4
elif price >= 1:
decimals = 5
else:
decimals = 6

return f"{price:.{decimals}f}"

============================================================

TWELVE DATA

============================================================

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
    logger.error(
        "Invalid Twelve Data response for %s",
        symbol,
    )
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
        "No candle data returned for %s",
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
        logger.error(
            "Not enough candles for %s: %s",
            symbol,
            len(df),
        )
        return None

    return df

except Exception as e:
    logger.exception(
        "Failed to parse candles for %s: %s",
        symbol,
        e,
    )
    return None

============================================================

2 MINUTE CANDLES

============================================================

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

============================================================

ATR

============================================================

def calculate_atr(
df: pd.DataFrame,
period: int = 10,
):
previous_close = df["close"].shift(1)

tr1 = (
    df["high"]
    - df["low"]
)

tr2 = (
    df["high"]
    - previous_close
).abs()

tr3 = (
    df["low"]
    - previous_close
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

============================================================

KELTNER CHANNEL

============================================================

def calculate_keltner(
df: pd.DataFrame,
ema_period: int = 20,
atr_period: int = 10,
multiplier: float = 2.0,
):
middle = (
df["close"]
.ewm(
span=ema_period,
adjust=False,
)
.mean()
)

atr = calculate_atr(
    df,
    atr_period,
)

upper = middle + (
    multiplier * atr
)

lower = middle - (
    multiplier * atr
)

return (
    middle,
    upper,
    lower,
)

============================================================

ADX

============================================================

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
    * (
        plus_di - minus_di
    ).abs()
    / denominator.replace(
        0,
        pd.NA,
    )
)

adx = dx.rolling(
    period
).mean()

return (
    adx,
    plus_di,
    minus_di,
)

============================================================

RSI

============================================================

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

============================================================

MARKET STRUCTURE

============================================================

def market_structure(
df: pd.DataFrame,
):
if len(df) < 8:
return "NEUTRAL"

recent = df.tail(8)

first_half = recent.iloc[:4]
second_half = recent.iloc[4:]

first_high = first_half["high"].max()
second_high = second_half["high"].max()

first_low = first_half["low"].min()
second_low = second_half["low"].min()

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

============================================================

PAIR SCORE

============================================================

def score_pair(
df: pd.DataFrame,
):
if len(df) < 40:
return -999, "NEUTRAL"

work = df.copy()

middle, upper, lower = calculate_keltner(
    work,
    20,
    10,
    2.0,
)

adx, plus_di, minus_di = calculate_adx(
    work,
    14,
)

rsi = calculate_rsi(
    work,
    14,
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

recent_change = (
    last["close"]
    - work.iloc[-5]["close"]
)

if recent_change > 0:
    score += 2

elif recent_change < 0:
    score -= 2

structure = market_structure(
    work
)

if structure == "BULLISH":
    score += 3

elif structure == "BEARISH":
    score -= 3

if pd.notna(last["adx"]):

    if last["adx"] >= 20:

        if (
            last["plus_di"]
            > last["minus_di"]
        ):
            score += 3

        elif (
            last["minus_di"]
            > last["plus_di"]
        ):
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

============================================================

CREATE CHART

============================================================

def create_chart(
df: pd.DataFrame,
symbol: str,
):
data = df.tail(40).copy()

middle, upper, lower = calculate_keltner(
    data,
    20,
    10,
    2.0,
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
        [
            low_price,
            high_price,
        ],
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
            )
            * 0.02,
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

x_values = range(
    len(data)
)

ax.plot(
    x_values,
    middle,
    linewidth=1.2,
    label="Keltner Middle",
)

ax.plot(
    x_values,
    upper,
    linewidth=0.8,
    linestyle="--",
    label="Keltner Upper",
)

ax.plot(
    x_values,
    lower,
    linewidth=0.8,
    linestyle="--",
    label="Keltner Lower",
)

ax.set_title(
    f"{symbol} · 2M"
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

ax.legend(
    loc="upper left"
)

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

============================================================

GEMINI PROMPT

============================================================

ANALYSIS_PROMPT = """
أنت محلل تقني متخصص في التداول قصير المدى على شارتات Forex.

حلل صورة الشارت بدقة وركز على آخر حركة سعرية.

الأولوية:

1. Price Action
2. فتح وإغلاق الشموع
3. High / Low
4. Market Structure
5. Breakout أو Failed Breakout
6. Liquidity Sweep / Rejection
7. Candle Confirmation
8. ADX + DMI
9. Keltner Channel 20/10
10. RSI كعامل ثانوي

القواعد:

- يجب اختيار اتجاه واحد فقط: UP أو DOWN.
- ممنوع NO SIGNAL.
- ممنوع WAIT.
- لا تعط اتجاهين.
- اختر الاتجاه الذي لديه أقوى الأدلة.
- لا تعتمد على شمعة واحدة فقط إذا كانت بنية السوق تعارضها.
- راقب Higher High / Higher Low للصعود.
- راقب Lower High / Lower Low للهبوط.
- راقب الإغلاق الحقيقي عند الاختراق.
- راقب Failed Breakout والرفض.
- استخدم Keltner وADX للتأكيد وليس بدل Price Action.
- RSI عامل مساعد فقط.
- حدد تأخير الدخول 1 أو 2 أو 3 دقائق.
- أعط وقت الدخول فقط، بدون وقت انتهاء.
- أعط سعر الدخول.
- أعط مستوى إلغاء واضح.
- UP: إلغاء إذا أغلقت شمعة تحت مستوى الإلغاء.
- DOWN: إلغاء إذا أغلقت شمعة فوق مستوى الإلغاء.
- السعر بحد أقصى 6 أرقام بعد الفاصلة.

أرجع JSON فقط:

{
"direction": "UP أو DOWN",
"confidence": 50,
"entry_delay_minutes": 1,
"entry_price": 0.0,
"cancellation_price": 0.0,
"reason": "سبب مختصر",
"structure": "BULLISH أو BEARISH أو NEUTRAL",
"momentum": "BULLISH أو BEARISH أو NEUTRAL"
}
"""

============================================================

GEMINI ANALYSIS

============================================================

async def analyze_with_gemini(
image_bytes: bytes,
symbol: str,
):

try:

    prompt = (
        ANALYSIS_PROMPT
        + "\n\nالزوج: "
        + symbol
    )

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

        if (
            start == -1
            or end == -1
            or end <= start
        ):
            raise RuntimeError(
                "Gemini did not return valid JSON"
            )

        result = json.loads(
            text[
                start:end + 1
            ]
        )

    if not isinstance(
        result,
        dict,
    ):
        raise RuntimeError(
            "Gemini result is not an object"
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

    if delay not in [
        1,
        2,
        3,
    ]:
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

    reason = str(
        result.get(
            "reason",
            "Price Action confirmation.",
        )
    )

    structure = str(
        result.get(
            "structure",
            "NEUTRAL",
        )
    ).upper()

    momentum = str(
        result.get(
            "momentum",
            "NEUTRAL",
        )
    ).upper()

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
        "reason": reason,
        "structure": structure,
        "momentum": momentum,
    }

except Exception as e:

    logger.exception(
        "Gemini analysis failed for %s: %s",
        symbol,
        e,
    )

    raise

============================================================

ENTRY TIME

============================================================

def calculate_entry_time(
delay_minutes: int,
):
now = datetime.now(
UTC_MINUS_3
)

entry = now + timedelta(
    minutes=delay_minutes
)

return entry.strftime(
    "%H:%M"
)

============================================================

FIND BEST PAIR

============================================================

async def find_best_pair():

logger.info(
    "Starting forex pair scan..."
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

for symbol, df in zip(
    FOREX_PAIRS,
    results,
):

    if isinstance(
        df,
        Exception,
    ):
        logger.error(
            "Pair %s failed: %s",
            symbol,
            df,
        )
        continue

    if df is None:
        continue

    try:

        candles_2m = make_2m_candles(
            df
        )

        score, direction = score_pair(
            candles_2m
        )

        if score > -999:

            candidates.append(
                {
                    "symbol": symbol,
                    "score": score,
                    "direction": direction,
                    "data": candles_2m,
                }
            )

            logger.info(
                "Pair %s score=%s direction=%s",
                symbol,
                score,
                direction,
            )

    except Exception as e:

        logger.exception(
            "Scoring failed for %s: %s",
            symbol,
            e,
        )

if not candidates:
    raise RuntimeError(
        "No valid forex pair data available"
    )

candidates.sort(
    key=lambda item: item["score"],
    reverse=True,
)

best = candidates[0]

logger.info(
    "Best pair selected: %s | score=%s | direction=%s",
    best["symbol"],
    best["score"],
    best["direction"],
)

return best

============================================================

SIGNAL FORMAT

============================================================

def format_signal(
symbol: str,
result: dict,
):

direction = result[
    "direction"
]

confidence = result[
    "confidence"
]

delay = result[
    "entry_delay_minutes"
]

entry_price = format_price(
    result[
        "entry_price"
    ]
)

cancellation_price = format_price(
    result[
        "cancellation_price"
    ]
)

entry_time = calculate_entry_time(
    delay
)

if direction == "UP":

    direction_text = (
        "🟢 UP — شراء"
    )

    cancel_text = (
        "إلغاء إذا أغلقت الشمعة تحت "
        f"{cancellation_price}"
    )

else:

    direction_text = (
        "🔴 DOWN — بيع"
    )

    cancel_text = (
        "إلغاء إذا أغلقت الشمعة فوق "
        f"{cancellation_price}"
    )

return (
    "🎓 تحليل زينو\n\n"
    f"🎯 Confidence: {confidence}%\n"
    f"📊 {symbol} · ⏱ 2M\n"
    "━━━━━━━━━━━━━━\n\n"
    f"🎯 القرار: {direction_text}\n"
    f"🕐 وقت الدخول: {entry_time}\n"
    f"⏳ بعد: {delay} دقيقة\n\n"
    f"💵 سعر الدخول: {entry_price}\n"
    f"🛑 {cancel_text}\n\n"
    f"📈 Market Structure: "
    f"{result['structure']}\n"
    f"⚡ Momentum: "
    f"{result['momentum']}\n\n"
    f"📝 السبب: {result['reason']}"
)

============================================================

START

============================================================

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

============================================================

GET SIGNAL

============================================================

async def get_signal(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):

if not is_owner(update):
    return

query = update.callback_query

await query.answer()

processing_message = await query.message.reply_text(
    "🔎 جاري فحص الأزواج..."
)

try:

    best = await find_best_pair()

    symbol = best[
        "symbol"
    ]

    candles = best[
        "data"
    ]

    await processing_message.edit_text(
        f"📊 {symbol}\n"
        "🔎 تم اختيار أفضل زوج بعد فحص الأزواج.\n"
        "📈 جاري إنشاء الشارت وتحليل الحركة..."
    )

    logger.info(
        "Creating chart for %s",
        symbol,
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
        "Chart created for %s: %s bytes",
        symbol,
        len(chart_bytes),
    )

    await processing_message.edit_text(
        f"📊 {symbol}\n"
        "🧠 جاري تحليل الشارت..."
    )

    result = await analyze_with_gemini(
        chart_bytes,
        symbol,
    )

    signal_text = format_signal(
        symbol,
        result,
    )

    await processing_message.delete()

    await query.message.reply_photo(
        photo=io.BytesIO(
            chart_bytes
        ),
        caption=signal_text,
    )

    logger.info(
        "Signal sent successfully for %s",
        symbol,
    )

except Exception as e:

    logger.exception(
        "Get Signal failed: %s",
        e,
    )

    error_text = (
        "❌ حدث خطأ أثناء التحليل:\n\n"
        f"{str(e)[:1000]}"
    )

    try:

        await processing_message.edit_text(
            error_text
        )

    except Exception:

        await query.message.reply_text(
            error_text
        )

============================================================

TEXT

============================================================

async def handle_text(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):

if not is_owner(update):
    return

await update.message.reply_text(
    "اضغط 🎯 Get Signal للحصول على الإشارة."
)

============================================================

PHOTO

============================================================

async def handle_photo(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):

if not is_owner(update):
    return

await update.message.reply_text(
    "🎯 استخدم Get Signal ليقوم البوت بفحص الأزواج وتحليل الشارت تلقائيًا."
)

============================================================

HEALTH SERVER

============================================================

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
    host="0.0.0.0",
    port=PORT,
)

await site.start()

logger.info(
    "HTTP server started on port %s",
    PORT,
)

return runner

============================================================

MAIN

============================================================

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

============================================================

RUN

============================================================

if name == "main":

try:

    asyncio.run(
        main()
    )

except KeyboardInterrupt:

    logger.info(
        "Bot stopped"
)
