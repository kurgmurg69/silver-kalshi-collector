from flask import Flask, request, jsonify
import json
from datetime import datetime

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Silver Kalshi Collector is running"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)

    if data is None:
        return jsonify({"ok": False, "error": "No JSON received"}), 400

    record = {
        "received_at_utc": datetime.utcnow().isoformat(),
        "data": data
    }

    print(json.dumps(record), flush=True)

    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
