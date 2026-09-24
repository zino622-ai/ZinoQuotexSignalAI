import os
import io
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import httpx
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from aiohttp import web
from google import genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(
format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
level=logging.INFO,
)

logger = logging.getLogger("ZinoQuotexSignalAI")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID_RAW = os.getenv("OWNER_ID")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
raise RuntimeError("GEMINI_API_KEY is missing")

if not OWNER_ID_RAW:
raise RuntimeError("OWNER_ID is missing")

if not TWELVE_DATA_API_KEY:
raise RuntimeError("TWELVE_DATA_API_KEY is missing")

try:
OWNER_ID = int(OWNER_ID_RAW)
except ValueError as exc:
raise RuntimeError("OWNER_ID must be an integer") from exc

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

PAIRS = [
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

TIMEFRAME = "2M"
MAX_SCAN_CONCURRENCY = 4

def is_owner(update: Update) -> bool:
user = update.effective_user
return bool(user and user.id == OWNER_ID)

def format_price(value) -> str:
try:
number = float(value)
except (TypeError, ValueError):
return str(value)

result = f"{number:.6f}".rstrip("0").rstrip(".")

if not result:
    return "0"

return result

async def get_forex_data(symbol: str, outputsize: int = 100) -> pd.DataFrame:
url = "https://api.twelvedata.com/time_series"

params = {
    "symbol": symbol,
    "interval": "1min",
    "outputsize": outputsize,
    "apikey": TWELVE_DATA_API_KEY,
    "format": "JSON",
}

async with httpx.AsyncClient(timeout=20.0) as client:
    response = await client.get(url, params=params)
    response.raise_for_status()
    data = response.json()

if data.get("status") == "error":
    raise RuntimeError(
        data.get("message", f"Twelve Data error for {symbol}")
    )

values = data.get("values")

if not values:
    raise RuntimeError(f"No market data for {symbol}")

df = pd.DataFrame(values)

required = [
    "datetime",
    "open",
    "high",
    "low",
    "close",
]

if any(column not in df.columns for column in required):
    raise RuntimeError(f"Invalid market data for {symbol}")

for column in ["open", "high", "low", "close"]:
    df[column] = pd.to_numeric(
        df[column],
        errors="coerce",
    )

df["datetime"] = pd.to_datetime(
    df["datetime"],
    errors="coerce",
)

df = df.dropna(
    subset=required
).sort_values(
    "datetime"
).reset_index(
    drop=True
)

if len(df) < 40:
    raise RuntimeError(
        f"Not enough candles for {symbol}"
    )

return df

def make_2m_candles(df: pd.DataFrame) -> pd.DataFrame:
data = df.copy().set_index("datetime")

result = data.resample(
    "2min",
    label="left",
    closed="left",
).agg(
    {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
).dropna().reset_index()

return result

def calculate_atr(
df: pd.DataFrame,
period: int = 10,
) -> pd.Series:

high = df["high"]
low = df["low"]
close = df["close"]

previous_close = close.shift(1)

true_range = pd.concat(
    [
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ],
    axis=1,
).max(axis=1)

return true_range.rolling(period).mean()

def calculate_keltner(
df: pd.DataFrame,
ema_period: int = 20,
atr_period: int = 10,
):

middle = df["close"].ewm(
    span=ema_period,
    adjust=False,
).mean()

atr = calculate_atr(
    df,
    atr_period,
)

upper = middle + (2.0 * atr)
lower = middle - (2.0 * atr)

return middle, upper, lower, atr

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
    np.where(
        (up_move > down_move) & (up_move > 0),
        up_move,
        0.0,
    ),
    index=df.index,
)

minus_dm = pd.Series(
    np.where(
        (down_move > up_move) & (down_move > 0),
        down_move,
        0.0,
    ),
    index=df.index,
)

previous_close = close.shift(1)

true_range = pd.concat(
    [
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ],
    axis=1,
).max(axis=1)

atr = true_range.rolling(period).mean()

plus_di = (
    100
    * plus_dm.rolling(period).mean()
    / atr.replace(0, np.nan)
)

minus_di = (
    100
    * minus_dm.rolling(period).mean()
    / atr.replace(0, np.nan)
)

denominator = (
    plus_di + minus_di
).replace(0, np.nan)

dx = (
    100
    * (plus_di - minus_di).abs()
    / denominator
)

adx = dx.rolling(period).mean()

return adx, plus_di, minus_di

def calculate_rsi(
df: pd.DataFrame,
period: int = 14,
) -> pd.Series:

delta = df["close"].diff()

gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

average_gain = gain.rolling(period).mean()
average_loss = loss.rolling(period).mean()

rs = (
    average_gain
    / average_loss.replace(0, np.nan)
)

rsi = 100 - (
    100 / (1 + rs)
)

rsi = rsi.where(
    average_loss != 0,
    100,
)

rsi = rsi.where(
    ~(
        (average_gain == 0)
        & (average_loss == 0)
    ),
    50,
)

return rsi

def candle_body(candle) -> float:
return abs(
float(candle["close"])
- float(candle["open"])
)

def get_market_structure(
df: pd.DataFrame,
) -> str:

if len(df) < 8:
    return "NEUTRAL"

recent = df.tail(8).reset_index(drop=True)

highs = recent["high"]
lows = recent["low"]
closes = recent["close"]

higher_high = (
    highs.iloc[-1]
    > highs.iloc[-3]
)

higher_low = (
    lows.iloc[-1]
    > lows.iloc[-3]
)

lower_high = (
    highs.iloc[-1]
    < highs.iloc[-3]
)

lower_low = (
    lows.iloc[-1]
    < lows.iloc[-3]
)

if (
    higher_high
    and higher_low
    and closes.iloc[-1]
    >= closes.iloc[-2]
):
    return "BULLISH"

if (
    lower_high
    and lower_low
    and closes.iloc[-1]
    <= closes.iloc[-2]
):
    return "BEARISH"

return "RANGE"

def score_pair(
df: pd.DataFrame,
):

middle, upper, lower, atr = calculate_keltner(df)

adx, plus_di, minus_di = calculate_adx(df)

rsi = calculate_rsi(df)

last = df.iloc[-1]
previous = df.iloc[-2]

direction_score = 0.0
reasons = []

if last["close"] > last["open"]:
    direction_score += 1.0
    reasons.append(
        "latest candle bullish"
    )

elif last["close"] < last["open"]:
    direction_score -= 1.0
    reasons.append(
        "latest candle bearish"
    )

if last["close"] > previous["high"]:
    direction_score += 2.0
    reasons.append(
        "bullish breakout"
    )

elif last["close"] < previous["low"]:
    direction_score -= 2.0
    reasons.append(
        "bearish breakout"
    )

structure = get_market_structure(df)

if structure == "BULLISH":
    direction_score += 2.0
    reasons.append(
        "bullish market structure"
    )

elif structure == "BEARISH":
    direction_score -= 2.0
    reasons.append(
        "bearish market structure"
    )

if (
    pd.notna(plus_di.iloc[-1])
    and pd.notna(minus_di.iloc[-1])
):

    if plus_di.iloc[-1] > minus_di.iloc[-1]:
        direction_score += 1.5
        reasons.append(
            "ADX directional pressure up"
        )

    elif minus_di.iloc[-1] > plus_di.iloc[-1]:
        direction_score -= 1.5
        reasons.append(
            "ADX directional pressure down"
        )

if (
    pd.notna(adx.iloc[-1])
    and adx.iloc[-1] >= 18
):
    reasons.append(
        "ADX trend strength present"
    )

if (
    pd.notna(upper.iloc[-1])
    and last["close"] >= upper.iloc[-1]
):
    direction_score -= 0.5
    reasons.append(
        "near Keltner upper band"
    )

elif (
    pd.notna(lower.iloc[-1])
    and last["close"] <= lower.iloc[-1]
):
    direction_score += 0.5
    reasons.append(
        "near Keltner lower band"
    )

if pd.notna(rsi.iloc[-1]):

    if rsi.iloc[-1] >= 70:
        direction_score -= 0.5
        reasons.append("RSI high")

    elif rsi.iloc[-1] <= 30:
        direction_score += 0.5
        reasons.append("RSI low")

body = candle_body(last)

candle_range = float(
    last["high"] - last["low"]
)

if candle_range > 0:
    body_ratio = body / candle_range
else:
    body_ratio = 0.0

if body_ratio >= 0.55:

    if last["close"] > last["open"]:
        direction_score += 1.0
        reasons.append(
            "strong bullish candle close"
        )

    elif last["close"] < last["open"]:
        direction_score -= 1.0
        reasons.append(
            "strong bearish candle close"
        )

return {
    "score": abs(direction_score),
    "raw_score": direction_score,
    "direction": (
        "UP"
        if direction_score >= 0
        else "DOWN"
    ),
    "structure": structure,
    "adx": (
        float(adx.iloc[-1])
        if pd.notna(adx.iloc[-1])
        else 0.0
    ),
    "rsi": (
        float(rsi.iloc[-1])
        if pd.notna(rsi.iloc[-1])
        else 50.0
    ),
    "atr": (
        float(atr.iloc[-1])
        if pd.notna(atr.iloc[-1])
        else 0.0
    ),
    "reasons": reasons[-5:],
}

def create_chart(
df: pd.DataFrame,
symbol: str,
) -> bytes:

data = df.tail(40).reset_index(drop=True)

middle, upper, lower, _ = calculate_keltner(data)

fig, ax = plt.subplots(
    figsize=(12, 6),
    dpi=130,
)

for index, row in data.iterrows():

    ax.plot(
        [index, index],
        [row["low"], row["high"]],
        linewidth=1,
    )

    ax.plot(
        [index, index],
        [row["open"], row["close"]],
        linewidth=5,
    )

ax.plot(
    middle.values,
    linewidth=1.2,
    label="Keltner Mid",
)

ax.plot(
    upper.values,
    linewidth=1.0,
    label="Keltner Upper",
)

ax.plot(
    lower.values,
    linewidth=1.0,
    label="Keltner Lower",
)

ax.set_title(
    f"ZinoQuotexSignalAI — {symbol} — 2M"
)

ax.set_xlabel(
    "2-minute candles"
)

ax.set_ylabel("Price")

ax.grid(alpha=0.2)

ax.legend(
    loc="upper left"
)

fig.tight_layout()

output = io.BytesIO()

fig.savefig(
    output,
    format="png",
    bbox_inches="tight",
)

plt.close(fig)

output.seek(0)

return output.getvalue()

ANALYSIS_PROMPT = """
You are the technical analysis engine for a private Quotex signal bot.

Analyze the supplied 2-minute Forex chart and calculated market data.

Priority:

1. Candle open/close and latest candle strength.
2. Recent lows/highs and breakouts or failed breakouts.
3. Market structure: higher highs/higher lows or lower highs/lower lows.
4. Liquidity sweep/rejection when visible.
5. Candle confirmation.
6. Keltner Channel 20/10.
7. ADX 14/14 and DI direction.
8. RSI 14 only as a secondary filter.

Rules:

- Return exactly one direction: UP or DOWN.
- Never return NO SIGNAL.
- Never return WAIT.
- Choose the direction with the strongest evidence.
- Entry delay must be an integer from 1 to 3 minutes.
- Prefer 1 minute when confirmation is already strong.
- Use 2 minutes when the next candle needs confirmation.
- Use 3 minutes only when additional confirmation is clearly needed.
- Entry price must be based on the latest available close.
- Cancellation level must be a nearby technical invalidation level.
- For UP: cancel if a candle closes below the cancellation level.
- For DOWN: cancel if a candle closes above the cancellation level.
- Price must use at most 6 decimal places.
- Do not invent indicators that are not visible or provided.

Return ONLY valid JSON:

{
"direction": "UP",
"confidence": 75,
"entry_delay": 2,
"entry_price": 1.234567,
"cancellation_price": 1.234000,
"market_structure": "BULLISH",
"momentum": "BULLISH",
"reason": "Short technical reason"
}
"""

def extract_json(text: str):

if not text:
    raise ValueError(
        "Gemini returned an empty response"
    )

cleaned = text.strip()

if cleaned.startswith("```"):
    cleaned = cleaned.replace(
        "```json",
        "",
        1,
    )

    cleaned = cleaned.replace(
        "```",
        "",
    ).strip()

start = cleaned.find("{")
end = cleaned.rfind("}")

if (
    start == -1
    or end == -1
    or end <= start
):
    raise ValueError(
        "Gemini did not return valid JSON"
    )

return json.loads(
    cleaned[start:end + 1]
)

async def analyze_with_gemini(
chart_bytes: bytes,
symbol: str,
metrics: dict,
):

image_part = {
    "inline_data": {
        "mime_type": "image/png",
        "data": chart_bytes,
    }
}

context = (
    f"Pair: {symbol}\n"
    f"Timeframe: 2M\n"
    f"Local pre-score direction: "
    f"{metrics['direction']}\n"
    f"Market structure: "
    f"{metrics['structure']}\n"
    f"ADX: {metrics['adx']:.2f}\n"
    f"RSI: {metrics['rsi']:.2f}\n"
    f"Pre-score reasons: "
    f"{', '.join(metrics['reasons'])}\n"
)

response = await asyncio.to_thread(
    gemini_client.models.generate_content,
    model=GEMINI_MODEL,
    contents=[
        ANALYSIS_PROMPT,
        context,
        image_part,
    ],
)

text = getattr(
    response,
    "text",
    None,
)

if not text:
    raise ValueError(
        "Gemini returned no text"
    )

result = extract_json(text)

direction = str(
    result.get(
        "direction",
        "",
    )
).upper()

if direction not in {"UP", "DOWN"}:
    direction = metrics["direction"]

try:
    confidence = int(
        float(
            result.get(
                "confidence",
                50,
            )
        )
    )
except (
    TypeError,
    ValueError,
):
    confidence = 50

confidence = max(
    50,
    min(99, confidence),
)

try:
    delay = int(
        float(
            result.get(
                "entry_delay",
                2,
            )
        )
    )
except (
    TypeError,
    ValueError,
):
    delay = 2

delay = max(
    1,
    min(3, delay),
)

try:
    entry_price = float(
        result["entry_price"]
    )
except (
    KeyError,
    TypeError,
    ValueError,
):
    entry_price = float(
        metrics["last_price"]
    )

try:
    cancellation = float(
        result["cancellation_price"]
    )
except (
    KeyError,
    TypeError,
    ValueError,
):
    cancellation = float(
        metrics["cancel_price"]
    )

if (
    direction == "UP"
    and cancellation >= entry_price
):
    cancellation = metrics[
        "cancel_price"
    ]

if (
    direction == "DOWN"
    and cancellation <= entry_price
):
    cancellation = metrics[
        "cancel_price"
    ]

structure = str(
    result.get(
        "market_structure",
        metrics["structure"],
    )
).upper()

momentum = str(
    result.get(
        "momentum",
        direction,
    )
).upper()

reason = str(
    result.get(
        "reason",
        "Price action confirmation.",
    )
)

return {
    "direction": direction,
    "confidence": confidence,
    "entry_delay": delay,
    "entry_price": entry_price,
    "cancellation_price": cancellation,
    "market_structure": structure,
    "momentum": momentum,
    "reason": reason[:500],
}

def build_fallback_analysis(
metrics: dict,
):

direction = metrics["direction"]

entry = metrics["last_price"]

cancellation = metrics[
    "cancel_price"
]

confidence = max(
    55,
    min(
        85,
        55 + int(
            metrics["score"] * 4
        ),
    ),
)

delay = (
    1
    if metrics["score"] >= 4
    else 2
)

return {
    "direction": direction,
    "confidence": confidence,
    "entry_delay": delay,
    "entry_price": entry,
    "cancellation_price": cancellation,
    "market_structure": metrics[
        "structure"
    ],
    "momentum": (
        "BULLISH"
        if direction == "UP"
        else "BEARISH"
    ),
    "reason": (
        "; ".join(
            metrics["reasons"]
        )
        or "Price action confirmation."
    ),
}

def prepare_metrics(
df: pd.DataFrame,
score_info: dict,
):

last = df.iloc[-1]
previous = df.iloc[-2]

if score_info["direction"] == "UP":

    cancel_price = min(
        float(last["low"]),
        float(previous["low"]),
    )

else:

    cancel_price = max(
        float(last["high"]),
        float(previous["high"]),
    )

return {
    **score_info,
    "last_price": float(
        last["close"]
    ),
    "cancel_price": cancel_price,
}

async def find_best_pair():

semaphore = asyncio.Semaphore(
    MAX_SCAN_CONCURRENCY
)

async def scan(symbol):

    async with semaphore:

        try:

            df_1m = await get_forex_data(
                symbol
            )

            df_2m = make_2m_candles(
                df_1m
            )

            if len(df_2m) < 30:
                return None

            score = score_pair(
                df_2m
            )

            metrics = prepare_metrics(
                df_2m,
                score,
            )

            return {
                "symbol": symbol,
                "df": df_2m,
                "metrics": metrics,
            }

        except Exception as exc:

            logger.warning(
                "Scan failed for %s: %s",
                symbol,
                exc,
            )

            return None

results = await asyncio.gather(
    *(
        scan(pair)
        for pair in PAIRS
    )
)

valid = [
    item
    for item in results
    if item is not None
]

if not valid:
    raise RuntimeError(
        "لم يتم الحصول على بيانات كافية من Twelve Data."
    )

valid.sort(
    key=lambda item: (
        item["metrics"]["score"],
        item["metrics"]["adx"],
    ),
    reverse=True,
)

return valid[0]

def get_entry_time(
delay_minutes: int,
) -> str:

now_utc = datetime.now(
    timezone.utc
)

target = (
    now_utc
    + timedelta(
        minutes=delay_minutes
    )
)

display_time = target.astimezone(
    timezone(
        timedelta(hours=-3)
    )
)

return display_time.strftime(
    "%H:%M:%S"
)

def format_signal(
symbol: str,
analysis: dict,
) -> str:

direction = analysis[
    "direction"
]

if direction == "UP":
    decision = (
        "🟢 UP — شراء (Call)"
    )

    cancellation_text = (
        "🛑 إلغاء إذا أغلقت شمعة "
        "تحت "
        f"{format_price(analysis['cancellation_price'])}"
    )

else:
    decision = (
        "🔴 DOWN — بيع (Put)"
    )

    cancellation_text = (
        "🛑 إلغاء إذا أغلقت شمعة "
        "فوق "
        f"{format_price(analysis['cancellation_price'])}"
    )

return (
    "🎓 تحليل زينو\n\n"
    f"🎯 Confidence: "
    f"{analysis['confidence']}%\n"
    f"📊 {symbol} · ⏱ 2M\n"
    "━━━━━━━━━━━━━━\n\n"
    f"🎯 القرار: {decision}\n"
    f"🕐 وقت الدخول: "
    f"{get_entry_time(analysis['entry_delay'])}\n"
    f"⏳ بعد "
    f"{analysis['entry_delay']} دقيقة\n\n"
    f"💵 سعر الدخول: "
    f"{format_price(analysis['entry_price'])}\n"
    f"{cancellation_text}\n\n"
    f"📈 Market Structure: "
    f"{analysis['market_structure']}\n"
    f"⚡ Momentum: "
    f"{analysis['momentum']}\n"
    f"📝 السبب: "
    f"{analysis['reason']}"
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

if update.message:
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

query = update.callback_query

if query:

    if not is_owner(update):

        await query.answer(
            "غير مسموح.",
            show_alert=True,
        )

        return

    await query.answer()

    message = query.message

else:

    if not is_owner(update):
        return

    message = update.effective_message

if not message:
    return

try:

    await message.edit_text(
        "🔎 جاري فحص الأزواج "
        "واختيار أفضل زوج..."
    )

    best = await find_best_pair()

    symbol = best["symbol"]
    df = best["df"]
    metrics = best["metrics"]

    await message.edit_text(
        f"📊 {symbol}\n"
        "🔎 تم اختيار أفضل زوج "
        "بعد فحص الأزواج.\n"
        "📈 جاري إنشاء الشارت..."
    )

    chart_bytes = await asyncio.to_thread(
        create_chart,
        df,
        symbol,
    )

    await message.edit_text(
        f"📊 {symbol}\n"
        "🧠 جاري تحليل الشارت..."
    )

    try:

        analysis = (
            await analyze_with_gemini(
                chart_bytes,
                symbol,
                metrics,
            )
        )

    except Exception as gemini_error:

        logger.exception(
            "Gemini analysis failed: %s",
            gemini_error,
        )

        analysis = (
            build_fallback_analysis(
                metrics
            )
        )

    caption = format_signal(
        symbol,
        analysis,
    )

    await message.reply_photo(
        photo=io.BytesIO(
            chart_bytes
        ),
        caption=caption,
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🎯 Get Signal",
                callback_data="get_signal",
            )
        ]
    ]

    await message.reply_text(
        "جاهز للإشارة التالية.",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

except Exception as exc:

    logger.exception(
        "Get Signal failed: %s",
        exc,
    )

    try:

        await message.edit_text(
            "❌ حدث خطأ أثناء التحليل:\n\n"
            f"{str(exc)[:1000]}"
        )

    except Exception:
        pass

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

runner = web.AppRunner(app)

await runner.setup()

site = web.TCPSite(
    runner,
    "0.0.0.0",
    PORT,
)

await site.start()

logger.info(
    "Health server running on port %s",
    PORT,
)

async def main():

await start_health_server()

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

logger.info(
    "ZinoQuotexSignalAI bot is starting..."
)

await application.initialize()

await application.start()

await application.updater.start_polling(
    drop_pending_updates=True
)

try:

    await asyncio.Event().wait()

finally:

    await application.updater.stop()

    await application.stop()

    await application.shutdown()

if name == "main":
asyncio.run(main())
