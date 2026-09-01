from flask import Flask, request, jsonify
import os
import json
import psycopg
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

KALSHI_API_BASE = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_SERIES = "KXSILVER15M"

# We only ask Kalshi for an update about once per minute,
# even though TradingView may hit our webhook every ~10 seconds.
last_kalshi_check = None


def get_db():
    return psycopg.connect(DATABASE_URL)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # -----------------------------
    # SILVER / TRADINGVIEW DATA
    # -----------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS silver_ticks (
            id BIGSERIAL PRIMARY KEY,
            received_at_utc TIMESTAMPTZ NOT NULL,
            symbol TEXT,
            bar_time BIGINT,
            open NUMERIC,
            high NUMERIC,
            low NUMERIC,
            close NUMERIC,
            volume NUMERIC,
            ema9 NUMERIC,
            ema20 NUMERIC,
            rsi14 NUMERIC,
            vwap NUMERIC,
            raw_json JSONB
        );
    """)

    # -----------------------------
    # KALSHI 15-MIN SILVER MARKETS
    # -----------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kalshi_silver_markets (
            id BIGSERIAL PRIMARY KEY,
            ticker TEXT UNIQUE NOT NULL,
            event_ticker TEXT,
            title TEXT,
            subtitle TEXT,

            status TEXT,
            result TEXT,

            open_time TIMESTAMPTZ,
            close_time TIMESTAMPTZ,
            expiration_time TIMESTAMPTZ,
            settlement_time TIMESTAMPTZ,

            strike_type TEXT,
            floor_strike NUMERIC,
            cap_strike NUMERIC,
            functional_strike TEXT,

            baseline NUMERIC,

            expiration_value TEXT,
            settlement_value TEXT,

            first_seen_utc TIMESTAMPTZ NOT NULL,
            last_seen_utc TIMESTAMPTZ NOT NULL,

            raw_json JSONB
        );
    """)

    conn.commit()
    cur.close()
    conn.close()


def parse_timestamp(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except Exception:
        return None


def safe_number(value):
    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None


def determine_baseline(market):
    """
    Kalshi exposes strike fields.

    For our Silver 15-minute markets, floor_strike is generally
    the number the market is comparing silver against.

    We still store all strike fields separately so we never lose
    the original Kalshi information.
    """

    floor_strike = safe_number(market.get("floor_strike"))
    cap_strike = safe_number(market.get("cap_strike"))

    if floor_strike is not None:
        return floor_strike

    if cap_strike is not None:
        return cap_strike

    return None


def fetch_kalshi_markets():
    params = urllib.parse.urlencode({
        "series_ticker": KALSHI_SERIES,
        "limit": 1000
    })

    url = f"{KALSHI_API_BASE}/markets?{params}"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Silver-Kalshi-Collector/1.0"
        }
    )

    with urllib.request.urlopen(req, timeout=15) as response:
        body = response.read().decode("utf-8")

    data = json.loads(body)

    return data.get("markets", [])


def save_kalshi_market(market):
    ticker = market.get("ticker")

    if not ticker:
        return

    now_utc = datetime.now(timezone.utc)

    baseline = determine_baseline(market)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO kalshi_silver_markets (
            ticker,
            event_ticker,
            title,
            subtitle,

            status,
            result,

            open_time,
            close_time,
            expiration_time,
            settlement_time,

            strike_type,
            floor_strike,
            cap_strike,
            functional_strike,

            baseline,

            expiration_value,
            settlement_value,

            first_seen_utc,
            last_seen_utc,

            raw_json
        )
        VALUES (
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s,
            %s, %s,
            %s, %s,
            %s
        )

        ON CONFLICT (ticker)
        DO UPDATE SET
            event_ticker = EXCLUDED.event_ticker,
            title = EXCLUDED.title,
            subtitle = EXCLUDED.subtitle,

            status = EXCLUDED.status,
            result = EXCLUDED.result,

            open_time = EXCLUDED.open_time,
            close_time = EXCLUDED.close_time,
            expiration_time = EXCLUDED.expiration_time,
            settlement_time = EXCLUDED.settlement_time,

            strike_type = EXCLUDED.strike_type,
            floor_strike = EXCLUDED.floor_strike,
            cap_strike = EXCLUDED.cap_strike,
            functional_strike = EXCLUDED.functional_strike,

            baseline = EXCLUDED.baseline,

            expiration_value = EXCLUDED.expiration_value,
            settlement_value = EXCLUDED.settlement_value,

            last_seen_utc = EXCLUDED.last_seen_utc,

            raw_json = EXCLUDED.raw_json;
    """, (
        ticker,
        market.get("event_ticker"),
        market.get("title"),
        market.get("subtitle"),

        market.get("status"),
        market.get("result"),

        parse_timestamp(market.get("open_time")),
        parse_timestamp(market.get("close_time")),
        parse_timestamp(market.get("expiration_time")),
        parse_timestamp(market.get("settlement_ts")),

        market.get("strike_type"),
        market.get("floor_strike"),
        market.get("cap_strike"),
        market.get("functional_strike"),

        baseline,

        market.get("expiration_value"),
        market.get("settlement_value_dollars"),

        now_utc,
        now_utc,

        json.dumps(market)
    ))

    conn.commit()
    cur.close()
    conn.close()


def update_kalshi():
    global last_kalshi_check

    now = datetime.now(timezone.utc)

    # Do not hit Kalshi every TradingView webhook.
    if last_kalshi_check is not None:
        seconds_since_last_check = (
            now - last_kalshi_check
        ).total_seconds()

        if seconds_since_last_check < 60:
            return

    last_kalshi_check = now

    try:
        markets = fetch_kalshi_markets()

        saved_count = 0

        for market in markets:
            save_kalshi_market(market)
            saved_count += 1

        print(
            f"KALSHI UPDATE COMPLETE: {saved_count} markets",
            flush=True
        )

    except Exception as e:
        print(
            "KALSHI UPDATE ERROR:",
            str(e),
            flush=True
        )


@app.route("/", methods=["GET"])
def home():
    return (
        "Silver Kalshi Collector is running. "
        "TradingView silver data and Kalshi "
        "15-minute silver markets are being collected."
    )


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)

    if data is None:
        return jsonify({
            "ok": False,
            "error": "No JSON received"
        }), 400

    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO silver_ticks (
                received_at_utc,
                symbol,
                bar_time,
                open,
                high,
                low,
                close,
                volume,
                ema9,
                ema20,
                rsi14,
                vwap,
                raw_json
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            );
        """, (
            datetime.now(timezone.utc),
            data.get("symbol"),
            data.get("time"),
            data.get("open"),
            data.get("high"),
            data.get("low"),
            data.get("close"),
            data.get("volume"),
            data.get("ema9"),
            data.get("ema20"),
            data.get("rsi14"),
            data.get("vwap"),
            json.dumps(data)
        ))

        conn.commit()
        cur.close()
        conn.close()

        print(
            "SAVED SILVER TICK:",
            data,
            flush=True
        )

        # TradingView keeps waking the server.
        # About once per minute we also collect Kalshi.
        update_kalshi()

        return jsonify({
            "ok": True,
            "silver_saved": True
        }), 200

    except Exception as e:
        print(
            "DATABASE ERROR:",
            str(e),
            flush=True
        )

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/latest", methods=["GET"])
def latest():
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                received_at_utc,
                symbol,
                bar_time,
                open,
                high,
                low,
                close,
                volume,
                ema9,
                ema20,
                rsi14,
                vwap
            FROM silver_ticks
            ORDER BY id DESC
            LIMIT 20;
        """)

        rows = cur.fetchall()

        cur.close()
        conn.close()

        return jsonify(rows)

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/kalshi", methods=["GET"])
def kalshi_latest():
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                ticker,
                status,
                result,
                open_time,
                close_time,
                baseline,
                strike_type,
                floor_strike,
                cap_strike,
                expiration_value,
                settlement_value,
                first_seen_utc,
                last_seen_utc
            FROM kalshi_silver_markets
            ORDER BY close_time DESC NULLS LAST
            LIMIT 50;
        """)

        rows = cur.fetchall()

        cur.close()
        conn.close()

        return jsonify(rows)

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/ledger", methods=["GET"])
def ledger():
    try:
        chicago = ZoneInfo("America/Chicago")

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                ticker,
                status,
                result,
                open_time,
                close_time,
                baseline,
                expiration_value,
                settlement_value
            FROM kalshi_silver_markets
            ORDER BY close_time DESC NULLS LAST
            LIMIT 100;
        """)

        rows = cur.fetchall()

        cur.close()
        conn.close()

        output = []

        for row in rows:
            (
                ticker,
                status,
                result,
                open_time,
                close_time,
                baseline,
                expiration_value,
                settlement_value
            ) = row

            open_ct = None
            close_ct = None

            if open_time:
                open_ct = open_time.astimezone(
                    chicago
                ).strftime(
                    "%Y-%m-%d %I:%M:%S %p CT"
                )

            if close_time:
                close_ct = close_time.astimezone(
                    chicago
                ).strftime(
                    "%Y-%m-%d %I:%M:%S %p CT"
                )

            output.append({
                "ticker": ticker,
                "status": status,
                "result": result,
                "open_CT": open_ct,
                "close_CT": close_ct,
                "baseline": (
                    str(baseline)
                    if baseline is not None
                    else None
                ),
                "expiration_value": expiration_value,
                "settlement_value": settlement_value
            })

        return jsonify(output)

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# Build the database tables when Render starts.
init_db()
