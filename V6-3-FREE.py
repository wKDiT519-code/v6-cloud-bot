import requests, pandas as pd, os, time, threading
from flask import Flask
from datetime import datetime

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
SYMBOL = "PAXGUSDT"
INTERVAL = "15m"
BINANCE_URL = "https://api.binance.com/api/v3"

app = Flask(__name__)

positions = []
highest = 0
last_hourly = 0
last_daily = 0

# === เก็บสถิติกำไร/ขาดทุน ===
stats = {
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "total_pnl_pct": 0.0,
    "best_trade": 0.0,
    "worst_trade": 0.0,
    "history": []  # เก็บ 20 ไม้ล่าสุด
}

@app.route('/')
def home():
    token_ok = "SET" if BOT_TOKEN else "MISSING"
    chat_ok = "SET" if CHAT_ID else "MISSING"
    avg_pnl = stats["total_pnl_pct"]/stats["total_trades"] if stats["total_trades"]>0 else 0
    winrate = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"""
V6.3 PNL TRACKER LIVE | {SYMBOL} {INTERVAL} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Positions: {len(positions)} | TotalTrades: {stats['total_trades']} | Wins: {stats['wins']} Losses: {stats['losses']}
Winrate: {winrate:.1f}% | TotalPnL: {stats['total_pnl_pct']:+.2f}% | Avg: {avg_pnl:+.2f}%
Best: {stats['best_trade']:+.2f}% Worst: {stats['worst_trade']:+.2f}%
<a href='/stats'>/stats</a> | <a href='/pnl'>/pnl ส่งสรุปเข้า Telegram</a> | <a href='/test-telegram'>/test-telegram</a>
"""

@app.route('/stats')
def stats_page():
    winrate = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    avg = stats["total_pnl_pct"]/stats["total_trades"] if stats["total_trades"]>0 else 0
    hist_html = "<br>".join([f"{h['time']} {h['type']} {h['entry']:.2f}->{h['exit']:.2f} {h['pnl']:+.2f}%" for h in stats["history"][-20:]])
    return f"""
<h2>📊 PAXGUSDT V6.3 สถิติ</h2>
Total Trades: {stats['total_trades']}<br>
Wins: {stats['wins']} Losses: {stats['losses']} Winrate: {winrate:.1f}%<br>
Total PnL: {stats['total_pnl_pct']:+.2f}%<br>
Avg per trade: {avg:+.2f}%<br>
Best: {stats['best_trade']:+.2f}% Worst: {stats['worst_trade']:+.2f}%<br>
<hr><b>20 ไม้ล่าสุด:</b><br>{hist_html}
"""

@app.route('/pnl')
def send_pnl_now():
    send_daily_summary(force=True)
    return "PNL summary sent to Telegram"

@app.route('/test-telegram')
def test_telegram():
    if not BOT_TOKEN or not CHAT_ID:
        return f"ENV MISSING", 500
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": "✅ TEST PNL TRACKER - ระบบสรุปกำไรพร้อมแล้ว!"}, timeout=15)
        return f"Telegram {r.status_code} {r.text}", r.status_code
    except Exception as e:
        return f"Error {e}", 500

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML"}, timeout=15)
    except Exception as e:
        print(f"TG Error {e}")

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
    except:
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

def close_position(price, close_type, last_ind=None):
    global positions, stats
    if not positions:
        return
    avg = sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
    pnl = (price-avg)/avg*100
    
    stats["total_trades"] += 1
    stats["total_pnl_pct"] += pnl
    if pnl>0:
        stats["wins"] += 1
    else:
        stats["losses"] += 1
    stats["best_trade"] = max(stats["best_trade"], pnl)
    stats["worst_trade"] = min(stats["worst_trade"], pnl)
    
    stats["history"].append({
        "time": datetime.now().strftime("%d/%m %H:%M"),
        "type": close_type,
        "entry": avg,
        "exit": price,
        "pnl": pnl
    })
    if len(stats["history"])>50:
        stats["history"] = stats["history"][-50:]

    winrate = stats["wins"]/stats["total_trades"]*100 if stats["total_trades"]>0 else 0
    
    if close_type=="FAST":
        send_telegram(f"✅ <b>ปิดเร็ว TP {pnl:+.2f}%</b> [{close_type}]\nเข้า {avg:.2f} → ออก {price:.2f}\n📊 สะสม: {stats['total_pnl_pct']:+.2f}% ({stats['total_trades']}ไม้ Winrate {winrate:.1f}%)")
    else:
        send_telegram(f"💰 <b>Trailing ปิด TP {pnl:+.2f}%</b> [{close_type}]\nเข้า {avg:.2f} → ออก {price:.2f}\n📊 สะสม: {stats['total_pnl_pct']:+.2f}% ({stats['total_trades']}ไม้ Winrate {winrate:.1f}%)")
    
    positions=[]

def send_daily_summary(force=False):
    global last_daily
    if not force and time.time() - last_daily < 82800:  # 23ชม
        return
    last_daily = time.time()
    if stats["total_trades"]==0:
        if force:
            send_telegram(f"📊 <b>สรุปกำไร/ขาดทุน PAXG V6.3</b>\nยังไม่มีเทรดที่ปิดเลยครับ\nรอสัญญาณ BB+STO+SRSI อยู่")
        return
    winrate = stats["wins"]/stats["total_trades"]*100 if stats["total_trades"]>0 else 0
    avg = stats["total_pnl_pct"]/stats["total_trades"] if stats["total_trades"]>0 else 0
    last5 = "\n".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%" for h in stats["history"][-5:]])
    msg = f"""📊 <b>สรุปกำไร/ขาดทุน V6.3</b>
⏰ {datetime.now().strftime('%d/%m %H:%M')}

💼 ทั้งหมด: {stats['total_trades']} ไม้
✅ ชนะ: {stats['wins']} ❌ แพ้: {stats['losses']}
🎯 Winrate: {winrate:.1f}%

💰 กำไรสะสม: {stats['total_pnl_pct']:+.2f}%
📈 เฉลี่ย/ไม้: {avg:+.2f}%
🏆 ดีสุด: {stats['best_trade']:+.2f}% แย่สุด: {stats['worst_trade']:+.2f}%

🕘 5 ไม้ล่าสุด:
{last5}

ดูเต็มๆ: https://v6-cloud-bot-1.onrender.com/stats
"""
    send_telegram(msg)

def trading_loop():
    global positions, highest, last_hourly, stats
    time.sleep(5)
    if BOT_TOKEN and CHAT_ID:
        send_telegram(f"🚀 <b>V6.3 PNL TRACKER เริ่มรันแล้ว</b>\nSYMBOL: {SYMBOL} {INTERVAL}\nStrategy: BB+STO+SRSI\nFast 1.2% + Trailing 1.5%/0.8% + แก้ไม้ 3\n<b>+ สรุปกำไร/ขาดทุน + รายงานชั่วโมง</b>\nดูสรุป: /stats หรือ /pnl")
    
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

            print(f"[{datetime.now().strftime('%H:%M:%S')}] {price:.2f} BUY:{buy_signal} POS:{len(positions)} Trades:{stats['total_trades']} PnL:{stats['total_pnl_pct']:.2f}%")

            # รายงานชั่วโมง
            if time.time() - last_hourly >= 3600:
                last_hourly = time.time()
                winrate = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                if len(positions)==0:
                    c1 = "✅" if cond1 else "❌"
                    c2 = "✅" if cond2 else "❌"
                    c3 = "✅" if cond3 else "❌"
                    status = f"⏳ รอสัญญาณ\n{c1} BBล่าง {price:.1f} vs {last['BB_L']:.1f}\n{c2} STO K {last['K']:.1f}\n{c3} SRSI K {last['SR_K']:.1f}"
                else:
                    avg = sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                    pnl = (price-avg)/avg*100
                    status = f"📊 ถือ {len(positions)}ไม้ PnL {pnl:+.2f}% Avg {avg:.2f}"
                
                msg = f"🕐 <b>ชั่วโมง {SYMBOL}</b> {datetime.now().strftime('%d/%m %H:%M')}\n💰 {price:.2f}$ BB_L {last['BB_L']:.2f} K {last['K']:.1f}/{last['D']:.1f} SR {last['SR_K']:.1f}\n{status}\n\n📊 สะสม {stats['total_pnl_pct']:+.2f}% {stats['total_trades']}ไม้ WR {winrate:.1f}%"
                send_telegram(msg)
                send_daily_summary()

            # เข้า
            if len(positions)==0 and buy_signal:
                positions.append({'entry': price, 'qty': 1})
                highest = price
                send_telegram(f"🟢 <b>LONG SIGNAL PAXG</b>\nราคา: {price:.2f}$\nBB_L: {last['BB_L']:.2f}\nSTO: {last['K']:.1f} > {last['D']:.1f}\nSRSI: {last['SR_K']:.1f} > {last['SR_D']:.1f}\nTP เร็ว: {price*1.012:.2f} (+1.2%)")

            # ออก
            if positions:
                avg = sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                pnl = (price-avg)/avg*100
                if price>highest: highest=price

                if pnl>=1.2 and sell_fast:
                    close_position(price, "FAST", last)
                elif pnl>=1.5 and price <= highest*0.992:
                    close_position(price, "TRAILING", last)
                elif price <= positions[-1]['entry']*0.988 and len(positions)<3 and buy_signal:
                    qty = 2**len(positions)
                    positions.append({'entry': price, 'qty': qty})
                    new_avg = sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                    send_telegram(f"🔧 <b>แก้ไม้ {len(positions)}</b>\nราคา {price:.2f} Qty {qty}\nเฉลี่ยใหม่ {new_avg:.2f}")

            time.sleep(60)
        except Exception as e:
            print(f"Loop err {e}")
            time.sleep(30)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
