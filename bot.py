import os
import io
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import httpx
import matplotlib
matplotlib.use("Agg")
importtlib matplotlib.pyplot as pfro
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
)

# ============================================================
# CONFIG
# ============================================================

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


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# TIMEZONE
# ============================================================

UTC_MINUS_3 = timezone(timedelta(hours=-3))


# ============================================================
# PAIRS TO SCAN
# ============================================================

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
    "CAD/JPY",
]


# ============================================================
# GEMINI PROMPT
# ============================================================

ANALYSIS_PROMPT = r"""
أنت محلل تقني متخصص في التداول قصير المدى.

حلل صورة الشارت المرفقة للزوج الذي تم اختياره مسبقاً.

أعطِ اتجاهًا واحدًا فقط:

UP أو DOWN.

لا تستخدم:
NO SIGNAL
WAIT

━━━━━━━━━━━━━━━━━━
الأولوية
━━━━━━━━━━━━━━━━━━

1. Price Action
2. Market Structure
3. Candle Close
4. Breakout / Rejection
5. ADX + DMI
6. Keltner Channel
7. RSI

━━━━━━━━━━━━━━━━━━
PRICE ACTION
━━━━━━━━━━━━━━━━━━

حلل:

Open
Close
High
Low
حجم الجسم
الظلال
القمم
القيعان
قوة الإغلاق

Higher High + Higher Low = Bullish.

Lower High + Lower Low = Bearish.

━━━━━━━━━━━━━━━━━━
MARKET STRUCTURE
━━━━━━━━━━━━━━━━━━

ابحث عن:

Break of Structure
Higher High
Higher Low
Lower High
Lower Low
Breakout
Failed Breakout
Liquidity Sweep
Rejection

━━━━━━━━━━━━━━━━━━
CANDLE CONFIRMATION
━━━━━━━━━━━━━━━━━━

Bullish:

Bullish Engulfing
Hammer
Bullish Rejection
Strong Bullish Close
Breakout Candle

Bearish:

Bearish Engulfing
Shooting Star
Bearish Rejection
Strong Bearish Close
Breakdown Candle

ركز على الشموع المغلقة.

━━━━━━━━━━━━━━━━━━
ADX + DMI
━━━━━━━━━━━━━━━━━━

ADX يقيس قوة الاتجاه.

ADX أقل من 20:
اتجاه ضعيف.

ADX فوق 20:
اتجاه محتمل.

ADX فوق 25:
قوة أعلى.

+DI > -DI:
يدعم UP.

-DI > +DI:
يدعم DOWN.

لا تستخدم ADX وحده.

━━━━━━━━━━━━━━━━━━
KELTNER
━━━━━━━━━━━━━━━━━━

استخدم Keltner كفلتر.

UP:
السعر فوق Middle Band مع momentum صاعد.

DOWN:
السعر تحت Middle Band مع momentum هابط.

لا تعكس الاتجاه فقط بسبب لمس Upper أو Lower Band.

━━━━━━━━━━━━━━━━━━
RSI
━━━━━━━━━━━━━━━━━━

RSI فلتر ثانوي.

فوق 70:
Overbought محتمل.

تحت 30:
Oversold محتمل.

لا تعكس اتجاه قوي بسبب RSI وحده.

━━━━━━━━━━━━━━━━━━
ENTRY DELAY
━━━━━━━━━━━━━━━━━━

اختر:

1
2
أو 3 دقائق.

1 دقيقة عندما يكون الاتجاه واضحاً.

2 دقيقة عند الحاجة إلى confirmation.

3 دقائق إذا كان السعر عند structure مهم ويحتاج شمعة إضافية.

━━━━━━━━━━━━━━━━━━
CANCELLATION
━━━━━━━━━━━━━━━━━━

UP:

close_below

DOWN:

close_above

يجب أن يكون المستوى قريباً من structure مهم وليس رقماً عشوائياً.

━━━━━━━━━━━━━━━━━━
CONFIDENCE
━━━━━━━━━━━━━━━━━━

50-59 ضعيف.

60-69 متوسط.

70-79 عدة confirmations.

80-89 توافق قوي.

90+ فقط في حالة استثنائية.

لا تستخدم 100%.

━━━━━━━━━━━━━━━━━━
OUTPUT
━━━━━━━━━━━━━━━━━━

JSON فقط:

{
  "asset": "EUR/USD",
  "timeframe": "2M",
  "direction": "UP",
  "confidence": 72,
  "entry_delay_minutes": 2,
  "entry_price": "1.123456",
  "cancellation_level": "1.122900",
  "cancellation_rule": "close_below",
  "trend": "Bullish",
  "market_structure": "Higher High + Higher Low",
  "momentum": "Bullish",
  "confirmation": "Bullish candle close",
  "adx_dmi": "Bullish",
  "keltner": "Price above middle band",
  "rsi": "Neutral/Bullish",
  "reason": "Bullish structure with confirmation."
}

لا تكتب Markdown.
لا تكتب أي شيء خارج JSON.
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
# HTTP REQUEST
# ============================================================

async def twelve_data_request(
    endpoint: str,
    params: dict,
) -> dict:

    url = f"https://api.twelvedata.com/{endpoint}"

    params = dict(params)

    params["apikey"] = TWELVE_DATA_API_KEY

    timeout = httpx.Timeout(
        connect=15.0,
        read=30.0,
        write=30.0,
        pool=15.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout
    ) as client:

        response = await client.get(
            url,
            params=params,
        )

    if response.status_code != 200:

        raise RuntimeError(
            f"Twelve Data HTTP {response.status_code}"
        )

    data = response.json()

    if data.get("status") == "error":

        raise RuntimeError(
            data.get(
                "message",
                "Twelve Data error"
            )
        )

    return data


# ============================================================
# GET CANDLES
# ============================================================

async def get_candles(
    symbol: str,
    outputsize: int = 120,
) -> list:

    data = await twelve_data_request(
        "time_series",
        {
            "symbol": symbol,
            "interval": "1min",
            "outputsize": outputsize,
            "format": "JSON",
        },
    )

    values = data.get("values")

    if not values:

        raise RuntimeError(
            f"No candle data for {symbol}"
        )

    candles = []

    for item in reversed(values):

        try:

            candles.append(
                {
                    "datetime": item["datetime"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                }
            )

        except Exception:
            continue

    if len(candles) < 30:

        raise RuntimeError(
            f"Not enough candles for {symbol}"
        )

    return candles


# ============================================================
# 2 MINUTE AGGREGATION
# ============================================================

def aggregate_2m(
    candles: list,
) -> list:

    result = []

    bucket = []

    for candle in candles:

        bucket.append(candle)

        if len(bucket) == 2:

            result.append(
                {
                    "datetime": bucket[0]["datetime"],
                    "open": bucket[0]["open"],
                    "high": max(
                        x["high"]
                        for x in bucket
                    ),
                    "low": min(
                        x["low"]
                        for x in bucket
                    ),
                    "close": bucket[-1]["close"],
                }
            )

            bucket = []

    return result


# ============================================================
# TECHNICAL HELPERS
# ============================================================

def ema(values: list, period: int) -> list:

    if not values:
        return []

    multiplier = 2 / (period + 1)

    result = [values[0]]

    for value in values[1:]:

        result.append(
            (
                value * multiplier
            )
            + (
                result[-1]
                * (1 - multiplier)
            )
        )

    return result


def true_ranges(candles: list) -> list:

    result = []

    previous_close = None

    for candle in candles:

        high = candle["high"]
        low = candle["low"]

        if previous_close is None:

            tr = high - low

        else:

            tr = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )

        result.append(tr)

        previous_close = candle["close"]

    return result


def calculate_atr(
    candles: list,
    period: int = 14,
) -> float:

    trs = true_ranges(candles)

    if len(trs) < period:
        return 0.0

    return sum(
        trs[-period:]
    ) / period


def calculate_rsi(
    candles: list,
    period: int = 14,
) -> float:

    closes = [
        c["close"]
        for c in candles
    ]

    if len(closes) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(closes)):

        change = (
            closes[i] - closes[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    avg_gain = sum(
        gains[-period:]
    ) / period

    avg_loss = sum(
        losses[-period:]
    ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# ============================================================
# SIMPLE ADX / DMI
# ============================================================

def calculate_dmi(
    candles: list,
    period: int = 14,
):

    if len(candles) < period + 2:

        return 0.0, 0.0, 0.0

    trs = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        high_diff = (
            current["high"]
            - previous["high"]
        )

        low_diff = (
            previous["low"]
            - current["low"]
        )

        tr = max(
            current["high"]
            - current["low"],
            abs(
                current["high"]
                - previous["close"]
            ),
            abs(
                current["low"]
                - previous["close"]
            ),
        )

        trs.append(tr)

        plus_dm.append(
            high_diff
            if (
                high_diff > low_diff
                and high_diff > 0
            )
            else 0.0
        )

        minus_dm.append(
            low_diff
            if (
                low_diff > high_diff
                and low_diff > 0
            )
            else 0.0
        )

    if len(trs) < period:
        return 0.0, 0.0, 0.0

    atr = (
        sum(trs[-period:])
        / period
    )

    if atr == 0:
        return 0.0, 0.0, 0.0

    plus_di = (
        100
        * (
            sum(plus_dm[-period:])
            / period
        )
        / atr
    )

    minus_di = (
        100
        * (
            sum(minus_dm[-period:])
            / period
        )
        / atr
    )

    denominator = (
        plus_di + minus_di
    )

    if denominator == 0:
        adx = 0.0

    else:

        adx = (
            100
            * abs(
                plus_di - minus_di
            )
            / denominator
        )

    return adx, plus_di, minus_di


# ============================================================
# KELTNER
# ============================================================

def calculate_keltner(
    candles: list,
):

    closes = [
        c["close"]
        for c in candles
    ]

    ema20 = ema(
        closes,
        20,
    )[-1]

    atr = calculate_atr(
        candles,
        10,
    )

    upper = (
        ema20
        + (2 * atr)
    )

    lower = (
        ema20
        - (2 * atr)
    )

    return ema20, upper, lower


# ============================================================
# SCORE ONE PAIR
# ============================================================

def score_pair(
    candles_1m: list,
):

    if len(candles_1m) < 40:

        return None

    candles_2m = aggregate_2m(
        candles_1m
    )

    if len(candles_2m) < 25:

        return None

    score_up = 0
    score_down = 0

    # --------------------------------------------------------
    # PRICE ACTION
    # --------------------------------------------------------

    last = candles_2m[-1]
    previous = candles_2m[-2]

    body = (
        last["close"]
        - last["open"]
    )

    candle_range = (
        last["high"]
        - last["low"]
    )

    if candle_range <= 0:
        return None

    body_ratio = (
        abs(body)
        / candle_range
    )

    if body > 0:
        score_up += 2

    elif body < 0:
        score_down += 2

    if body_ratio >= 0.55:

        if body > 0:
            score_up += 2
        else:
            score_down += 2

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    recent = candles_2m[-8:]

    highs = [
        c["high"]
        for c in recent
    ]

    lows = [
        c["low"]
        for c in recent
    ]

    if (
        highs[-1] > highs[-3]
        and lows[-1] > lows[-3]
    ):

        score_up += 3

    elif (
        highs[-1] < highs[-3]
        and lows[-1] < lows[-3]
    ):

        score_down += 3

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    previous_high = max(
        c["high"]
        for c in candles_2m[-8:-1]
    )

    previous_low = min(
        c["low"]
        for c in candles_2m[-8:-1]
    )

    if last["close"] > previous_high:

        score_up += 3

    if last["close"] < previous_low:

        score_down += 3

    # --------------------------------------------------------
    # ADX + DMI
    # --------------------------------------------------------

    adx, plus_di, minus_di = (
        calculate_dmi(
            candles_2m,
            14,
        )
    )

    if adx >= 20:

        if plus_di > minus_di:
            score_up += 2

        elif minus_di > plus_di:
            score_down += 2

    if adx >= 25:

        if plus_di > minus_di:
            score_up += 1

        elif minus_di > plus_di:
            score_down += 1

    # --------------------------------------------------------
    # KELTNER
    # --------------------------------------------------------

    middle, upper, lower = (
        calculate_keltner(
            candles_2m
        )
    )

    if last["close"] > middle:

        score_up += 2

    elif last["close"] < middle:

        score_down += 2

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = calculate_rsi(
        candles_2m,
        14,
    )

    if 52 <= rsi <= 68:

        score_up += 1

    elif 32 <= rsi <= 48:

        score_down += 1

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    if (
        last["close"]
        > previous["close"]
    ):

        score_up += 1

    elif (
        last["close"]
        < previous["close"]
    ):

        score_down += 1

    difference = abs(
        score_up - score_down
    )

    total = (
        score_up
        + score_down
    )

    if total == 0:
        return None

    direction = (
        "UP"
        if score_up >= score_down
        else "DOWN"
    )

    confidence = int(
        min(
            89,
            55
            + (
                difference
                * 4
            ),
        )
    )

    # Need meaningful edge
    if difference < 3:
        confidence = min(
            confidence,
            62,
        )

    return {
        "direction": direction,
        "score_up": score_up,
        "score_down": score_down,
        "difference": difference,
        "confidence": confidence,
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "rsi": rsi,
        "keltner_middle": middle,
        "keltner_upper": upper,
        "keltner_lower": lower,
        "candles_1m": candles_1m,
        "candles_2m": candles_2m,
    }


# ============================================================
# SCAN PAIRS
# ============================================================

async def scan_pairs():

    logger.info(
        "Starting market scan..."
    )

    tasks = [
        get_candles(
            pair,
            120,
        )
        for pair in PAIRS
    ]

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

    candidates = []

    for pair, result in zip(
        PAIRS,
        results,
    ):

        if isinstance(
            result,
            Exception,
        ):

            logger.warning(
                "Failed %s: %s",
                pair,
                result,
            )

            continue

        try:

            analysis = score_pair(
                result
            )

            if not analysis:
                continue

            analysis["asset"] = pair

            candidates.append(
                analysis
            )

        except Exception as exc:

            logger.warning(
                "Scoring failed %s: %s",
                pair,
                exc,
            )

    if not candidates:

        raise RuntimeError(
            "لم يتم الحصول على بيانات كافية من Twelve Data."
        )

    candidates.sort(
        key=lambda x: (
            x["confidence"],
            x["difference"],
            x["adx"],
        ),
        reverse=True,
    )

    best = candidates[0]

    logger.info(
        "Selected %s | %s | confidence=%s",
        best["asset"],
        best["direction"],
        best["confidence"],
    )

    return best


# ============================================================
# CREATE CHART
# ============================================================

def create_chart(
    asset: str,
    candles: list,
) -> bytes:

    candles = candles[-60:]

    closes = [
        c["close"]
        for c in candles
    ]

    opens = [
        c["open"]
        for c in candles
    ]

    highs = [
        c["high"]
        for c in candles
    ]

    lows = [
        c["low"]
        for c in candles
    ]

    fig, ax = plt.subplots(
        figsize=(12, 6)
    )

    width = 0.55

    for i in range(
        len(candles)
    ):

        open_price = opens[i]
        close_price = closes[i]
        high_price = highs[i]
        low_price = lows[i]

        ax.vlines(
            i,
            low_price,
            high_price,
            linewidth=1,
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

            height = (
                max(highs)
                - min(lows)
            ) * 0.002

        ax.add_patch(
            plt.Rectangle(
                (
                    i - width / 2,
                    bottom,
                ),
                width,
                height,
                fill=False,
                linewidth=1.2,
            )
        )

    ax.set_title(
        f"{asset} — ZinoQuotexSignalAI"
    )

    ax.set_xlabel(
        "1-minute candles"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.20
    )

    plt.tight_layout()

    buffer = io.BytesIO()

    fig.savefig(
        buffer,
        format="png",
        dpi=140,
    )

    plt.close(fig)

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# GEMINI ANALYSIS
# ============================================================

async def analyze_chart(
    image_bytes: bytes,
    asset: str,
    timeframe: str,
) -> dict:

    import base64

    image_base64 = (
        base64.b64encode(
            image_bytes
        ).decode("utf-8")
    )

    prompt = (
        ANALYSIS_PROMPT
        + "\n\n"
        + f"الزوج المختار: {asset}\n"
        + f"الفريم المطلوب تحليله: {timeframe}\n"
    )

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    },
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": image_base64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.15,
            "responseMimeType": "application/json",
        },
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

    async with httpx.AsyncClient(
        timeout=timeout
    ) as client:

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
                .get(
                    "message",
                    response.text,
                )
            )

        except Exception:

            message = response.text

        raise RuntimeError(
            f"Gemini HTTP {response.status_code}: "
            f"{message}"
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
            "",
        )

        text = text.replace(
            "```",
            "",
        )

        text = text.strip()

    try:

        return json.loads(text)

    except json.JSONDecodeError:

        raise RuntimeError(
            "Gemini returned invalid JSON"
        )


# ============================================================
# VALIDATE
# ============================================================

def validate_signal(
    data: dict,
    selected_asset: str,
) -> dict:

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

    data["asset"] = selected_asset

    direction = str(
        data["direction"]
    ).upper()

    if direction not in (
        "UP",
        "DOWN",
    ):

        raise RuntimeError(
            "Invalid direction"
        )

    confidence = int(
        data["confidence"]
    )

    if not 0 <= confidence <= 100:

        raise RuntimeError(
            "Invalid confidence"
        )

    delay = int(
        data["entry_delay_minutes"]
    )

    if delay not in (
        1,
        2,
        3,
    ):

        raise RuntimeError(
            "Invalid entry delay"
        )

    rule = str(
        data["cancellation_rule"]
    ).lower()

    if direction == "UP":

        if rule != "close_below":

            raise RuntimeError(
                "Invalid UP cancellation rule"
            )

    else:

        if rule != "close_above":

            raise RuntimeError(
                "Invalid DOWN cancellation rule"
            )

    data["direction"] = direction
    data["confidence"] = confidence
    data["entry_delay_minutes"] = delay
    data["cancellation_rule"] = rule

    return data


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    data: dict,
) -> str:

    direction = data["direction"]

    if direction == "UP":

        direction_text = (
            "🟢 UP — شراء (Call)"
        )

        cancel_text = (
            "🛑 إلغاء إذا أغلقت شمعة تحت "
            f"{data['cancellation_level']}"
        )

        trend_emoji = "📈"

    else:

        direction_text = (
            "🔴 DOWN — بيع (Put)"
        )

        cancel_text = (
            "🛑 إلغاء إذا أغلقت شمعة فوق "
            f"{data['cancellation_level']}"
        )

        trend_emoji = "📉"

    now = datetime.now(
        UTC_MINUS_3
    )

    entry_time = (
        now
        + timedelta(
            minutes=int(
                data[
                    "entry_delay_minutes"
                ]
            )
        )
    )

    entry_time_text = (
        entry_time.strftime(
            "%H:%M"
        )
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
        "🎯 اضغط Get Signal",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# ============================================================
# GET SIGNAL
# ============================================================

async def get_signal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    if query.from_user.id != OWNER_ID:

        await query.answer()

        return

    await query.answer()

    processing = (
        await query.message.reply_text(
            "🔎 جاري فحص الأزواج..."
        )
    )

    try:

        # ----------------------------------------------------
        # 1. SCAN
        # ----------------------------------------------------

        best = await scan_pairs()

        asset = best["asset"]

        candles_2m = best[
            "candles_2m"
        ]

        # ----------------------------------------------------
        # 2. CREATE CHART
        # ----------------------------------------------------

        chart_bytes = create_chart(
            asset,
            candles_2m,
        )

        # ----------------------------------------------------
        # 3. SEND CHART FIRST
        # ----------------------------------------------------

        await query.message.reply_photo(
            photo=io.BytesIO(
                chart_bytes
            ),
            caption=(
                f"📊 {asset}\n"
                f"🔎 تم اختيار أفضل زوج "
                f"بعد فحص الأزواج."
            ),
        )

        # ----------------------------------------------------
        # 4. GEMINI ANALYSIS
        # ----------------------------------------------------

        await processing.edit_text(
            f"🧠 تحليل {asset}..."
        )

        result = await analyze_chart(
            chart_bytes,
            asset,
            "2M",
        )

        result = validate_signal(
            result,
            asset,
        )

        # ----------------------------------------------------
        # 5. SEND SIGNAL
        # ----------------------------------------------------

        keyboard = [
            [
                InlineKeyboardButton(
                    "🎯 Get Signal",
                    callback_data="get_signal",
                )
            ]
        ]

        await processing.edit_text(
            format_signal(result),
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

    except Exception as exc:

        logger.exception(
            "Get Signal error"
        )

        await processing.edit_text(
            "❌ حدث خطأ:\n\n"
            f"{str(exc)}"
        )


# ============================================================
# HEALTH SERVER
# ============================================================

async def health_server():

    from aiohttp import web

    async def health(
        request,
    ):

        return web.Response(
            text=(
                "ZinoQuotexSignalAI is running"
            )
        )

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
        "Health server running on %s",
        PORT,
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
            start,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            get_signal,
            pattern="^get_signal$",
        )
    )

    runner = await health_server()

    try:

        await application.initialize()

        await application.start()

        await application.updater.start_polling(
            drop_pending_updates=True
        )

        logger.info(
            "ZinoQuotexSignalAI started"
        )

        await asyncio.Event().wait()

    finally:

        await application.updater.stop()

        await application.stop()

        await application.shutdown()

        await runner.cleanup()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped"
)
