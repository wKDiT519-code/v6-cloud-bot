import requests, os, time, threading
from flask import Flask

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
SYMBOL = "PAXGUSDT"

app = Flask(__name__)

@app.route('/')
def home():
    status = "SET" if BOT_TOKEN else "MISSING"
    chat = "SET" if CHAT_ID else "MISSING"
    return f"V6.3 BOT Running - {SYMBOL} - BOT_TOKEN:{status} CHAT_ID:{chat} - {time.strftime('%Y-%m-%d %H:%M:%S')}"

@app.route('/test-telegram')
def test_telegram():
    if not BOT_TOKEN or not CHAT_ID:
        return f"ENV MISSING! BOT_TOKEN:{bool(BOT_TOKEN)} CHAT_ID:{bool(CHAT_ID)} - Go to Render Settings > Environment", 500
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": "✅ TEST จาก Render - ถ้าเห็นข้อความนี้คือตั้งค่าถูกแล้ว!", "parse_mode":"HTML"}, timeout=15)
        return f"Telegram status: {r.status_code} - {r.text}", r.status_code
    except Exception as e:
        return f"Error: {e}", 500

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID in env - Check Render Environment tab")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML"}, timeout=15)
        print(f"Telegram status: {r.status_code} {r.text[:500]}")
    except Exception as e:
        print(f"Telegram error: {e}")

def price_loop():
    time.sleep(8)
    print(f"Starting price loop. BOT_TOKEN set: {bool(BOT_TOKEN)}, CHAT_ID set: {bool(CHAT_ID)}")
    if BOT_TOKEN and CHAT_ID:
        send_telegram(f"🚀 <b>V6.3 CLOUD FREE เริ่มรันแล้ว</b>\n\nSYMBOL: {SYMBOL} 15m\nRender: v6-cloud-bot-1 LIVE\nเวลา: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\nถ้าเห็นข้อความนี้ = ตั้งค่าถูกหมดแล้ว!")
    else:
        print("Bot started but env missing")
    
    while True:
        try:
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}", timeout=10).json()
            price = float(r.get('price', 0))
            print(f"[{time.strftime('%H:%M:%S')}] {SYMBOL}: {price}")
        except Exception as e:
            print(f"Price error: {e}")
        time.sleep(60)

threading.Thread(target=price_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
