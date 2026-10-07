import requests, pandas as pd, os, time, threading, ccxt
from flask import Flask
from datetime import datetime

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

SYMBOL = "PAXGUSDT"
RENDER_URL = "https://v6-cloud-bot-1.onrender.com"
INTERVAL = "15m"

ORDER_PERCENT = 10
LEVERAGE = 3

app = Flask(__name__)
positions, highest, last_hourly = [], 0, 0
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"best_trade":0.0,"worst_trade":0.0,"history":[]}

mexc_public = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
mexc = ccxt.mexc({'apiKey': MEXC_API_KEY, 'secret': MEXC_API_SECRET, 'enableRateLimit': True, 'options': {'defaultType': 'swap'}}) if MEXC_API_KEY and MEXC_API_SECRET else None

@app.route('/')
def home():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"V6.3 MEXC FUT 10% LIVE | {SYMBOL} {INTERVAL} | Trades:{stats['total_trades']} WR:{wr:.1f}% | stats"

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist_html = ""
    for h in stats["history"][-20:]:
        hist_html += f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>"
    return f"<h2>PAXGUSDT MEXC FUTURES 10%</h2>ราคา MEXC Futures 100%<br>ไม้แรก 10% แก้ 20% 40% x{LEVERAGE}<br>Trades:{stats['total_trades']} WR:{wr:.1f}% Total:{stats['total_pnl_pct']:+.2f}%<br><hr>{hist_html}"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print(f"TG err {e}")

def get_price_mexc():
    try:
        ticker = mexc_public.fetch_ticker(SYMBOL)
        price = ticker.get('last') or ticker.get('close')
        if price: return float(price)
    except Exception as e:
        print(f"Price err {e}")
    try:
        r = requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol": "PAXG_USDT"}, timeout=10)
        data = r.json()
        return float(data['data']['lastPrice'])
    except:
        return None

def get_klines_mexc(limit=100):
    try:
        ohlcv = mexc_public.fetch_ohlcv(SYMBOL, timeframe='15m', limit=limit)
        df = pd.DataFrame(ohlcv, columns=['ot','o','h','l','c','v'])
        return df
    except Exception as e:
        print(f"Kline err {e}")
        return None

def calc(df):
    df['BB_mid']=df['c'].rolling(20).mean()
    df['BB_std']=df['c'].rolling(20).std()
    df['BB_L']=df['BB_mid']-2*df['BB_std']
    low_min=df['l'].rolling(14).min()
    high_max=df['h'].rolling(14).max()
    df['K']=100*(df['c']-low_min)/(high_max-low_min)
    df['D']=df['K'].rolling(3).mean()
    delta=df['c'].diff()
    gain=delta.where(delta>0,0).rolling(14).mean()
    loss=-delta.where(delta<0,0).rolling(14).mean()
    df['RSI']=100-(100/(1+gain/loss))
    rsi_low=df['RSI'].rolling(14).min()
    rsi_high=df['RSI'].rolling(14).max()
    df['SR_K']=100*(df['RSI']-rsi_low)/(rsi_high-rsi_low)
    df['SR_D']=df['SR_K'].rolling(3).mean()
    return df.dropna()

def get_balance():
    if not mexc: return 0
    try:
        bal = mexc.fetch_balance()
        total = bal.get('USDT',{}).get('total') or bal.get('total',{}).get('USDT') or bal.get('USDT',{}).get('free',0) or 0
        return float(total)
    except Exception as e:
        print(f"Bal err {e}")
        return 0

def mexc_buy(price, factor):
    if not mexc:
        send_telegram(f"จำลอง BUY {ORDER_PERCENT*factor}% @ MEXC FUT {price:.2f}")
        return True
    try:
        try:
            mexc.set_leverage(LEVERAGE, SYMBOL, {'marginMode': 'ISOLATED'})
        except:
            pass
        bal = get_balance()
        usdt = bal * (ORDER_PERCENT/100) * factor
        if usdt < 5: usdt = 5
        qty = round(usdt/price, 4)
        if qty < 0.001: qty = 0.001
        order = mexc.create_market_buy_order(SYMBOL, qty)
        send_telegram(f"✅ MEXC FUT BUY {ORDER_PERCENT*factor}% สำเร็จ พอร์ต ${bal:.2f} ใช้ ${usdt:.2f} x{LEVERAGE} ราคา {price:.2f} Qty {qty} ID {order.get('id')}")
        return True
    except Exception as e:
        send_telegram(f"❌ BUY ล้มเหลว {str(e)[:350]}")
        return False

def mexc_close():
    if not mexc: return True
    try:
        try:
            mexc.create_market_sell_order(SYMBOL, None, {'closePosition': True})
        except:
            poss=mexc.fetch_positions([SYMBOL])
            for p in poss:
                c=float(p.get('contracts',0) or 0)
                if c!=0:
                    mexc.create_market_order(SYMBOL, 'sell' if c>0 else 'buy', abs(c), None, {'reduceOnly': True})
        return True
    except Exception as e:
        send_telegram(f"❌ CLOSE ล้มเหลว {str(e)[:250]}")
        return False

def close_position(price, typ):
    global positions, stats
    if not positions: return
    avg=sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
    pnl=(price-avg)/avg*100
    stats["total_trades"]+=1
    stats["total_pnl_pct"]+=pnl
    if pnl>0: stats["wins"]+=1
    else: stats["losses"]+=1
    stats["best_trade"]=max(stats["best_trade"], pnl)
    stats["worst_trade"]=min(stats["worst_trade"], pnl)
    stats["history"].append({"time": datetime.now().strftime("%d/%m %H:%M"), "type": typ, "entry": avg, "exit": price, "pnl": pnl})
    mexc_close()
    wr=stats["wins"]/stats["total_trades"]*100 if stats["total_trades"]>0 else 0
    send_telegram(f"✅ {typ} ปิด {pnl:+.2f}% {avg:.2f}->{price:.2f} สะสม {stats['total_pnl_pct']:+.2f}% WR {wr:.1f}% {RENDER_URL}/stats")
    positions.clear()

def trading_loop():
    global positions, highest, last_hourly
    time.sleep(5)
    send_telegram(f"🚀 V6.3 MEXC FUT 10% เริ่มแล้ว ราคา MEXC Futures 100% ไม้ 10% 20% 40% x{LEVERAGE} พอร์ต ${get_balance():.2f} {RENDER_URL}/stats")
    while True:
        try:
            price=get_price_mexc()
            df=get_klines_mexc()
            if not price or df is None or len(df)<30:
                time.sleep(10)
                continue
            df=calc(df)
            last=df.iloc[-1]
            prev=df.iloc[-2]
            cond1=price <= last['BB_L']*1.0015
            cond2=prev['K'] < prev['D'] and last['K'] > last['D'] and last['K'] < 35
            cond3=prev['SR_K'] < prev['SR_D'] and last['SR_K'] > last['SR_D'] and last['SR_K'] < 40
            buy_signal=cond1 and cond2 and cond3
            sell_fast=last['K'] > 78 or last['SR_K'] > 85

            if time.time()-last_hourly >= 3600:
                last_hourly=time.time()
                bal=get_balance()
                if len(positions)==0:
                    status=f"Balance ${bal:.2f} ไม้ละ 10% = ${bal*0.1:.2f}"
                else:
                    avg=sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                    pnl=(price-avg)/avg*100
                    status=f"ถือ {len(positions)}ไม้ PnL {pnl:+.2f}%"
                send_telegram(f"MEXC FUT {price:.2f}$ {status} BB_L {last['BB_L']:.2f} K {last['K']:.1f} SR {last['SR_K']:.1f} {RENDER_URL}/stats")

            if len(positions)==0 and buy_signal:
                mexc_buy(price, 1)
                positions.append({'entry': price, 'qty': 1})
                highest=price
                send_telegram(f"🟢 LONG MEXC FUT 10% {price:.2f}$ BB_L {last['BB_L']:.2f} K {last['K']:.1f} SR {last['SR_K']:.1f}")

            if positions:
                avg=sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                pnl=(price-avg)/avg*100
                if price>highest:
                    highest=price
                if pnl>=1.2 and sell_fast:
                    close_position(price, "FAST")
                elif pnl>=1.5 and price <= highest*0.992:
                    close_position(price, "TRAILING")
                elif price <= positions[-1]['entry']*0.988 and len(positions)<3 and buy_signal:
                    factor=2**len(positions)
                    mexc_buy(price, factor)
                    positions.append({'entry': price, 'qty': factor})
                    send_telegram(f"🔧 แก้ไม้ {len(positions)} {10*factor}% MEXC FUT {price:.2f}")

            time.sleep(60)
        except Exception as e:
            print(f"Loop err {e}")
            time.sleep(30)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
