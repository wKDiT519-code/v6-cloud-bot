import requests, pandas as pd, time, os, threading
from flask import Flask

# === CONFIG ของคุณ ===
BOT_TOKEN = "8445500532:AAHQzou7eaBqXgXr6w3U3bI3b2oJzVv3Zv3Zv3Zv3Zv"  # จะถูก revoke ได้
CHAT_ID = "8959859282"
SYMBOL = "PAXGUSDT"
INTERVAL = "15m"

# Flask หลอก Render ให้เป็น Web Service ฟรี
app = Flask(__name__)
@app.route('/')
def home():
    return "🚀 V6.3 BOT is Running - PAXG 4148$"

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except: pass

def trading_loop():
    send_telegram(f"🚀 V6.3 CLOUD FREE เริ่มรันแล้ว\nSYMBOL: {SYMBOL} 15m\nราคาเปิดวันนี้ 4166$ | ตอนนี้ 4183$")
    while True:
        try:
            # เช็คราคา Binance
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}", timeout=10).json()
            price = float(r['price'])
            # TODO: ใส่ logic BB+STO+SRSI ของ V6.2 ที่นี่
            print(f"Price: {price}")
        except Exception as e:
            print(e)
        time.sleep(60)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
