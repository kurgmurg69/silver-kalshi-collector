from flask import Flask, request, jsonify
import os
import json
import psycopg2
from datetime import datetime, timezone

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db()
    cur = conn.cursor()

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

    conn.commit()
    cur.close()
    conn.close()


@app.route("/", methods=["GET"])
def home():
    return "Silver Kalshi Collector is running and saving to Postgres"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)

    if data is None:
        return jsonify({"ok": False, "error": "No JSON received"}), 400

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

        return jsonify({"ok": True}), 200

    except Exception as e:
        print(f"DATABASE ERROR: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/latest", methods=["GET"])
def latest():
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


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=10000)
