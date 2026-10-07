import requests, os, time, threading
from flask import Flask

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
SYMBOL = "PAXGUSDT"

app = Flask(__name__)

@app.route('/')
def home():
    return f"🚀 V6-CLOUD-BOT LIVE | {SYMBOL} | {time.strftime('%Y-%m-%d %H:%M:%S')}"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID in env")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML"}, timeout=15)
        print(f"Telegram status: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"Telegram error: {e}")

def price_loop():
    time.sleep(5)
    if BOT_TOKEN and CHAT_ID:
        send_telegram(f"🚀 <b>V6.3 CLOUD FREE เริ่มรันแล้ว</b>\n\nSYMBOL: {SYMBOL} 15m\nXAU เปิดวันนี้ 4166$\nสถานะ: เชื่อมต่อ Binance + Telegram สำเร็จ\nRender: v6-cloud-bot-1 LIVE")
    else:
        print("Bot started but env missing, check Render Environment tab")
    
    while True:
        try:
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}", timeout=10).json()
            price = float(r['price'])
            print(f"[{time.strftime('%H:%M:%S')}] {SYMBOL}: {price}")
            # เพิ่ม logic BB+STO+SRSI V6.2 ตรงนี้ได้
        except Exception as e:
            print(f"Price error: {e}")
        time.sleep(60)

threading.Thread(target=price_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
