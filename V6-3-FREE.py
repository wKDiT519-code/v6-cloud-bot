import requests, pandas as pd, os, time, threading
from flask import Flask
from datetime import datetime

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
SYMBOL = "PAXGUSDT"
INTERVAL = "15m"
BINANCE_URL = "https://api.binance.com/api/v3"
RENDER_URL = "https://v6-cloud-bot-1.onrender.com"

app = Flask(__name__)

positions = []
highest = 0
last_hourly = 0
last_daily = 0

stats = {
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "total_pnl_pct": 0.0,
    "best_trade": 0.0,
    "worst_trade": 0.0,
    "history": []
}

@app.route('/')
def home():
    winrate = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"V6.3 FINAL LINKS LIVE | {SYMBOL} | Trades:{stats['total_trades']} PnL:{stats['total_pnl_pct']:+.2f}% WR:{winrate:.1f}% | <a href='/stats'>stats</a> <a href='/pnl'>pnl</a>"

@app.route('/stats')
def stats_page():
    winrate = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    avg = stats["total_pnl_pct"]/stats["total_trades"] if stats["total_trades"]>0 else 0
    hist = "<br>".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%" for h in stats["history"][-20:]])
    return f"<h2>📊 PAXGUSDT V6.3</h2>Trades:{stats['total_trades']} Wins:{stats['wins']} Losses:{stats['losses']} WR:{winrate:.1f}%<br>Total:{stats['total_pnl_pct']:+.2f}% Avg:{avg:+.2f}%<br>Best:{stats['best_trade']:+.2f}% Worst:{stats['worst_trade']:+.2f}%<br><hr>{hist}"

@app.route('/pnl')
def send_pnl_now():
    send_daily_summary(force=True)
    return "PNL sent - check Telegram"

@app.route('/test-telegram')
def test_telegram():
    if not BOT_TOKEN or not CHAT_ID:
        return "ENV MISSING", 500
    send_telegram(f"✅ <b>TEST ลิงค์กดได้</b>\n\n📊 ดูสถิติเต็มๆ กดเลย:\n{RENDER_URL}/stats\n\n💰 สั่งสรุป PnL เข้า Telegram กดเลย:\n{RENDER_URL}/pnl")
    return "Sent with clickable links"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
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

def close_position(price, close_type):
    global positions, stats
    if not positions:
        return
    avg = sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
    pnl = (price-avg)/avg*100
    stats["total_trades"] += 1
    stats["total_pnl_pct"] += pnl
    if pnl>0: stats["wins"]+=1
    else: stats["losses"]+=1
    stats["best_trade"]=max(stats["best_trade"], pnl)
    stats["worst_trade"]=min(stats["worst_trade"], pnl)
    stats["history"].append({"time": datetime.now().strftime("%d/%m %H:%M"), "type": close_type, "entry": avg, "exit": price, "pnl": pnl})
    if len(stats["history"])>50: stats["history"]=stats["history"][-50:]
    winrate = stats["wins"]/stats["total_trades"]*100 if stats["total_trades"]>0 else 0
    send_telegram(f"✅ <b>{close_type} ปิด +{pnl:.2f}%</b>\nเข้า {avg:.2f} → ออก {price:.2f}\n📊 สะสม: {stats['total_pnl_pct']:+.2f}% ({stats['total_trades']}ไม้ WR {winrate:.1f}%)\n\nดูเต็มๆ: {RENDER_URL}/stats")
    positions.clear()

def send_daily_summary(force=False):
    global last_daily
    if not force and time.time()-last_daily < 82800:
        return
    last_daily=time.time()
    if stats["total_trades"]==0:
        if force:
            send_telegram(f"📊 <b>สรุปกำไร/ขาดทุน V6.3</b>\nยังไม่มีเทรดที่ปิดเลยครับ\n\n📈 ดูสถิติ: {RENDER_URL}/stats\n💰 กดสรุปอีกที: {RENDER_URL}/pnl")
        return
    winrate = stats["wins"]/stats["total_trades"]*100 if stats["total_trades"]>0 else 0
    avg = stats["total_pnl_pct"]/stats["total_trades"] if stats["total_trades"]>0 else 0
    last5 = "\n".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%" for h in stats["history"][-5:]])
    msg = f"""📊 <b>สรุปกำไร/ขาดทุน V6.3</b>
⏰ {datetime.now().strftime('%d/%m %H:%M')}

💼 ทั้งหมด: {stats['total_trades']} ไม้ ✅{stats['wins']} ❌{stats['losses']} WR {winrate:.1f}%
💰 สะสม: {stats['total_pnl_pct']:+.2f}% เฉลี่ย {avg:+.2f}%
🏆 ดีสุด {stats['best_trade']:+.2f}% แย่สุด {stats['worst_trade']:+.2f}%

🕘 5 ไม้ล่าสุด:
{last5}

📈 ดูเต็มๆ: {RENDER_URL}/stats
🔄 สรุปอีกครั้ง: {RENDER_URL}/pnl
"""
    send_telegram(msg)

def trading_loop():
    global positions, highest, last_hourly
    time.sleep(5)
    if BOT_TOKEN and CHAT_ID:
        send_telegram(f"🚀 <b>V6.3 FINAL เริ่มรันแล้ว</b>\nSYMBOL: {SYMBOL} {INTERVAL}\nStrategy: BB+STO+SRSI Fast 1.2% Trailing 1.5%/0.8% แก้ไม้ 3\n\n<b>ลิงค์กดดูได้เลย:</b>\n📊 สถิติทั้งหมด: {RENDER_URL}/stats\n💰 สั่งสรุป PnL: {RENDER_URL}/pnl\n\nรายงานชั่วโมง + สรุปกำไรทำงานแล้ว ✅")
    
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

            if time.time() - last_hourly >= 3600:
                last_hourly = time.time()
                winrate = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                if len(positions)==0:
                    c1="✅" if cond1 else "❌"
                    c2="✅" if cond2 else "❌"
                    c3="✅" if cond3 else "❌"
                    status=f"⏳ รอสัญญาณ\n{c1} BBล่าง {price:.1f} vs {last['BB_L']:.1f}\n{c2} STO K {last['K']:.1f}\n{c3} SRSI K {last['SR_K']:.1f}"
                else:
                    avg=sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                    pnl=(price-avg)/avg*100
                    status=f"📊 ถือ {len(positions)}ไม้ PnL {pnl:+.2f}%"
                msg=f"🕐 <b>ชั่วโมง {SYMBOL}</b> {datetime.now().strftime('%d/%m %H:%M')}\n💰 {price:.2f}$ BB_L {last['BB_L']:.2f} K {last['K']:.1f} SR {last['SR_K']:.1f}\n{status}\n📊 สะสม {stats['total_pnl_pct']:+.2f}% {stats['total_trades']}ไม้ WR {winrate:.1f}%\n\n📈 {RENDER_URL}/stats"
                send_telegram(msg)
                send_daily_summary()

            if len(positions)==0 and buy_signal:
                positions.append({'entry': price, 'qty': 1})
                highest=price
                send_telegram(f"🟢 <b>LONG SIGNAL PAXG</b>\nราคา: {price:.2f}$\nBB_L: {last['BB_L']:.2f}\nSTO: {last['K']:.1f} > {last['D']:.1f}\nSRSI: {last['SR_K']:.1f} > {last['SR_D']:.1f}\n\n📊 {RENDER_URL}/stats")

            if positions:
                avg=sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                pnl=(price-avg)/avg*100
                if price>highest: highest=price
                if pnl>=1.2 and sell_fast:
                    close_position(price, "FAST")
                elif pnl>=1.5 and price <= highest*0.992:
                    close_position(price, "TRAILING")
                elif price <= positions[-1]['entry']*0.988 and len(positions)<3 and buy_signal:
                    qty=2**len(positions)
                    positions.append({'entry': price, 'qty': qty})
                    new_avg=sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                    send_telegram(f"🔧 <b>แก้ไม้ {len(positions)}</b>\nราคา {price:.2f} Qty {qty}\nเฉลี่ยใหม่ {new_avg:.2f}\n\n📊 {RENDER_URL}/stats")

            time.sleep(60)
        except Exception as e:
            print(f"Loop err {e}")
            time.sleep(30)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
