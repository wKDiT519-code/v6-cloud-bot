"""
V6.3 CLOUD FINAL - PAXGUSDT + Telegram Real-time
TOKEN: 8445500532:AAHQzou7ea49VQcu5ZNw44XqWgG9p8gC5uo
CHAT_ID: 8959859282
Deploy: Render.com / Railway / VPS - pip install requests pandas
Strategy: BB(20,2) lower + STO cross up <35 + SRSI cross up <40 - Fast 1.2% + Trailing 0.8% + แก้ไม้ 3 ไม้
"""
import requests
import pandas as pd
import time
from datetime import datetime

# CONFIG - ใส่ของคุณแล้ว
BOT_TOKEN = "8445500532:AAHQzou7ea49VQcu5ZNw44XqWgG9p8gC5uo"
CHAT_ID = "8959859282"
SYMBOL = "PAXGUSDT"
INTERVAL = "15m"

BINANCE_URL = "https://api.binance.com/api/v3"
TELE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

def send_telegram(msg):
    try:
        requests.post(TELE_URL, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        print(f"TG Sent: {msg}")
    except Exception as e:
        print(f"TG Error: {e}")

def get_price():
    try:
        r = requests.get(f"{BINANCE_URL}/ticker/price", params={"symbol": SYMBOL}, timeout=10)
        return float(r.json()['price'])
    except:
        return None

def get_klines(limit=100):
    try:
        r = requests.get(f"{BINANCE_URL}/klines", params={"symbol": SYMBOL, "interval": INTERVAL, "limit": limit}, timeout=10)
        data = r.json()
        df = pd.DataFrame(data, columns=['ot','o','h','l','c','v','ct','qav','tr','tb','tq','ig'])
        for col in ['o','h','l','c','v']:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        print(f"Kline err {e}")
        return None

def calc(df):
    df['BB_mid'] = df['c'].rolling(20).mean()
    df['BB_std'] = df['c'].rolling(20).std()
    df['BB_L'] = df['BB_mid'] - 2*df['BB_std']
    df['BB_U'] = df['BB_mid'] + 2*df['BB_std']
    low_min = df['l'].rolling(14).min()
    high_max = df['h'].rolling(14).max()
    df['K'] = 100*(df['c']-low_min)/(high_max-low_min)
    df['D'] = df['K'].rolling(3).mean()
    delta = df['c'].diff()
    gain = delta.where(delta>0,0).rolling(14).mean()
    loss = -delta.where(delta<0,0).rolling(14).mean()
    rs = gain/loss
    df['RSI'] = 100-(100/(1+rs))
    rsi_low = df['RSI'].rolling(14).min()
    rsi_high = df['RSI'].rolling(14).max()
    df['SR_K'] = 100*(df['RSI']-rsi_low)/(rsi_high-rsi_low)
    df['SR_D'] = df['SR_K'].rolling(3).mean()
    return df.dropna()

positions = []
highest = 0

send_telegram(f"🚀 V6.3 CLOUD เริ่มรันแล้ว\nSYMBOL: {SYMBOL} {INTERVAL}\nStrategy: BB+STO+SRSI Fast 1.2% + Trailing 0.8%\nราคาเริ่มต้นตรวจทุก 60วิ")

while True:
    try:
        price = get_price()
        df = get_klines()
        if price is None or df is None:
            time.sleep(10)
            continue
        df = calc(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        cond1 = price <= last['BB_L']*1.0015
        cond2 = prev['K'] < prev['D'] and last['K'] > last['D'] and last['K'] < 35
        cond3 = prev['SR_K'] < prev['SR_D'] and last['SR_K'] > last['SR_D'] and last['SR_K'] < 40
        buy_signal = cond1 and cond2 and cond3
        sell_fast = last['K'] > 78 or last['SR_K'] > 85

        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] {price:.2f} BB_L {last['BB_L']:.2f} K {last['K']:.1f}/{last['D']:.1f} SR {last['SR_K']:.1f}/{last['SR_D']:.1f} BUY:{buy_signal} POS:{len(positions)}")

        if len(positions)==0 and buy_signal:
            positions.append({'entry': price, 'qty': 1})
            highest = price
            send_telegram(f"🟢 *LONG SIGNAL PAXG*\nราคา: {price:.2f}$\nBB_L: {last['BB_L']:.2f}\nSTO: {last['K']:.1f}>{last['D']:.1f}\nSRSI: {last['SR_K']:.1f}>{last['SR_D']:.1f}\nTP เร็ว: {price*1.012:.2f} (+1.2%)\nแก้ไม้ 1: {price*0.988:.2f}")

        if positions:
            avg = sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
            pnl = (price-avg)/avg*100
            if price>highest: highest=price

            if pnl>=1.2 and sell_fast:
                send_telegram(f"✅ *ปิดเร็ว +{pnl:.2f}%*\nเข้า {avg:.2f} → ออก {price:.2f}\nสูงสุด {highest:.2f}")
                positions=[]

            if pnl>=1.5 and price <= highest*0.992:
                send_telegram(f"💰 *Trailing ปิด +{pnl:.2f}%*\nเข้า {avg:.2f} → ออก {price:.2f}\nHighest {highest:.2f}")
                positions=[]

            if price <= positions[-1]['entry']*0.988 and len(positions)<3 and buy_signal:
                qty = 2**len(positions)
                positions.append({'entry': price, 'qty': qty})
                new_avg = sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                send_telegram(f"🔧 *แก้ไม้ {len(positions)}*\nราคา {price:.2f} Qty {qty}\nเฉลี่ยใหม่ {new_avg:.2f}")

        time.sleep(60)
    except Exception as e:
        print(f"Loop err {e}")
        time.sleep(30)
