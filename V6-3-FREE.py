import requests, pandas as pd, os, time, threading, ccxt
from flask import Flask
from datetime import datetime
import pytz
MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
SYMBOL = "PAXGUSDT"
RENDER_URL = "https://v6-cloud-bot-1.onrender.com"
INTERVAL = "15m"
ORDER_PERCENT = 10
LEVERAGE = 3
TP_PCT = 3.2
SL_PCT = 1.8
MAX_WOODS = 3
COOLDOWN_HOURS = 4
MAX_HOLD_HOURS = 96
AVOID_FUNDING_MINUTES = 5
FUNDING_TIMES_UTC = [0, 8, 16]
NEWS_BLACKOUT = True
app = Flask(__name__)
positions, highest, lowest, last_hourly = [], 0, 999999, 0
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[]}
cooldown_until = 0
mexc_public = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
mexc = ccxt.mexc({'apiKey': MEXC_API_KEY, 'secret': MEXC_API_SECRET, 'enableRateLimit': True, 'options': {'defaultType': 'swap'}}) if MEXC_API_KEY and MEXC_API_SECRET else None
def thai_time():
    return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")
def is_funding_time():
    try:
        utc_hour = datetime.now(pytz.timezone('UTC')).hour
        utc_min = datetime.now(pytz.timezone('UTC')).minute
        for fh in FUNDING_TIMES_UTC:
            if utc_hour == fh and utc_min >= (60 - AVOID_FUNDING_MINUTES): return True, f"Funding {fh}:00 UTC"
            if utc_hour == fh and utc_min <= AVOID_FUNDING_MINUTES: return True, f"หลัง Funding {fh}:00 UTC"
        return False, ""
    except: return False, ""
def is_news_time():
    if not NEWS_BLACKOUT: return False, ""
    try:
        now_th = datetime.now(pytz.timezone('Asia/Bangkok'))
        if now_th.weekday() == 4 and 1 <= now_th.day <= 7 and 19 <= now_th.hour <= 20:
            return True, f"Non-Farm {now_th.strftime('%d/%m')} 19:30 ICT"
        return False, ""
    except: return False, ""
def check_max_hold():
    if not positions: return False
    try:
        ft = positions[0].get('time',0)
        return (time.time()-ft)/3600 >= MAX_HOLD_HOURS if ft else False
    except: return False
@app.route('/')
def home():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    cm = f" | Cooldown {(cooldown_until-time.time())/3600:.1f}h" if cooldown_until>time.time() else ""
    return f"V95 FINAL LIVE Trades:{stats['total_trades']} WR:{wr:.1f}%{cm}"
@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-20:]])
    return f"<h2>V95 FINAL COOLDOWN {COOLDOWN_HOURS}h MAX_HOLD {MAX_HOLD_HOURS}h</h2>Trades:{stats['total_trades']} WR:{wr:.1f}% Total:{stats['total_pnl_pct']:+.2f}%<br><hr>{hist}"
def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML"}, timeout=15)
    except: pass
def get_price_mexc():
    try:
        t=mexc_public.fetch_ticker(SYMBOL)
        return float(t.get('last') or t.get('close'))
    except:
        try:
            r=requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol":"PAXG_USDT"}, timeout=10)
            return float(r.json()['data']['lastPrice'])
        except: return None
def get_klines_mexc(limit=150):
    try:
        ohlcv=mexc_public.fetch_ohlcv(SYMBOL, timeframe='15m', limit=limit)
        return pd.DataFrame(ohlcv, columns=['ot','o','h','l','c','v'])
    except: return None
def calc(df):
    df['BB_mid']=df['c'].rolling(20).mean()
    df['BB_std']=df['c'].rolling(20).std()
    df['BB_U']=df['BB_mid']+2*df['BB_std']
    df['BB_L']=df['BB_mid']-2*df['BB_std']
    df['K']=100*(df['c']-df['l'].rolling(14).min())/(df['h'].rolling(14).max()-df['l'].rolling(14).min())
    df['D']=df['K'].rolling(3).mean()
    delta=df['c'].diff()
    df['RSI']=100-(100/(1+delta.where(delta>0,0).rolling(14).mean()/-delta.where(delta<0,0).rolling(14).mean()))
    return df.dropna()
def check_divergence(df, lookback=20):
    if len(df)<lookback*2: return False,False,""
    try:
        recent=df.iloc[-lookback:]
        prev=df.iloc[-lookback*2:-lookback]
        bull=recent['l'].min() < prev['l'].min()*0.998 and recent.loc[recent['l'].idxmin(),'RSI'] > prev.loc[prev['l'].idxmin(),'RSI']+2
        bear=recent['h'].max() > prev['h'].max()*1.002 and recent.loc[recent['h'].idxmax(),'RSI'] < prev.loc[prev['h'].idxmax(),'RSI']-2
        return bull,bear,f"DIV BULL={bull} BEAR={bear}"
    except: return False,False,""
def get_balance():
    if not mexc: return 0
    try:
        bal=mexc.fetch_balance()
        return float(bal.get('USDT',{}).get('total') or 0)
    except: return 0
def format_balance_msg():
    bal=get_balance()
    wr=(stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"💰 พอร์ต ${bal:.2f} | สะสม {stats['total_pnl_pct']:+.2f}% | WR {wr:.1f}%", {"total":bal,"upnl":0}, wr
def mexc_buy(price,factor):
    if not mexc:
        send_telegram(f"[{thai_time()}] จำลอง BUY {factor}")
        return True,1
    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL, {'marginMode':'ISOLATED'})
        except: pass
        bal=get_balance()
        qty=round(max(bal*(ORDER_PERCENT/100)*factor,5)/price,4)
        mexc.create_market_buy_order(SYMBOL, max(qty,0.001))
        send_telegram(f"✅ [{thai_time()}] BUY ไม้{factor} @ {price:.2f}")
        return True,qty
    except Exception as e:
        send_telegram(f"❌ BUY Fail {e}")
        return False,0
def mexc_sell(price,factor):
    if not mexc:
        send_telegram(f"[{thai_time()}] จำลอง SHORT {factor}")
        return True,1
    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL, {'marginMode':'ISOLATED'})
        except: pass
        bal=get_balance()
        qty=round(max(bal*(ORDER_PERCENT/100)*factor,5)/price,4)
        mexc.create_market_sell_order(SYMBOL, max(qty,0.001))
        send_telegram(f"✅ [{thai_time()}] SHORT ไม้{factor} @ {price:.2f}")
        return True,qty
    except Exception as e:
        send_telegram(f"❌ SHORT Fail {e}")
        return False,0
def mexc_close(side='long'):
    if not mexc: return True
    try:
        mexc.create_market_sell_order(SYMBOL, None, {'closePosition':True}) if side!='short' else mexc.create_market_buy_order(SYMBOL, None, {'closePosition':True})
        return True
    except:
        try:
            for p in mexc.fetch_positions([SYMBOL]):
                c=float(p.get('contracts',0) or 0)
                if c!=0: mexc.create_market_order(SYMBOL, 'sell' if c>0 else 'buy', abs(c), None, {'reduceOnly':True})
            return True
        except: return False
def close_position(price,typ):
    global positions,stats,cooldown_until
    if not positions: return
    avg=sum(p['entry']*abs(p['qty']) for p in positions)/sum(abs(p['qty']) for p in positions)
    is_short=any(p.get('qty',0)<0 for p in positions)
    pnl=(avg-price)/avg*100 if is_short else (price-avg)/avg*100
    stats["total_trades"]+=1
    stats["total_pnl_pct"]+=pnl
    if pnl>0: stats["wins"]+=1
    else: stats["losses"]+=1
    stats["history"].append({"time":thai_time(),"type":typ,"pnl":pnl})
    mexc_close('short' if is_short else 'long')
    bal_msg,_,wr=format_balance_msg()
    if pnl<0 and len(positions)>=MAX_WOODS:
        cooldown_until=time.time()+COOLDOWN_HOURS*3600
        send_telegram(f"✅ [{thai_time()}] {typ} ปิดขาดทุน {pnl:+.2f}% ครบ {len(positions)}ไม้ หยุด {COOLDOWN_HOURS}ชม.\n{bal_msg}")
    else:
        send_telegram(f"✅ [{thai_time()}] {typ} ปิด {pnl:+.2f}% WR {wr:.1f}%\n{bal_msg}")
    positions.clear()
def trading_loop():
    global positions,highest,lowest,last_hourly,cooldown_until
    time.sleep(5)
    bal_msg,_,_=format_balance_msg()
    send_telegram(f"🚀 [{thai_time()}] V95 FINAL + COOLDOWN + FUNDING/NFP เริ่มแล้ว ไม้1=10% ไม้2=20% ไม้3=40%\n{bal_msg}")
    while True:
        try:
            price=get_price_mexc()
            df=get_klines_mexc()
            if not price or df is None or len(df)<60:
                time.sleep(10)
                continue
            df=calc(df)
            last=df.iloc[-1]
            prev=df.iloc[-2]
            bb_low=any(df['c'].iloc[-3:].values <= df['BB_L'].iloc[-3:].values*1.0015)
            bb_high=any(df['c'].iloc[-3:].values >= df['BB_U'].iloc[-3:].values*0.9985)
            buy=bb_low and prev['K']<prev['D'] and last['K']>last['D'] and last['K']<40 and last['RSI']<42
            short=bb_high and prev['K']>prev['D'] and last['K']<last['D'] and last['K']>60 and last['RSI']>58
            bull,bear,div=check_divergence(df,20)
            if cooldown_until>time.time():
                time.sleep(60)
                continue
            if time.time()-last_hourly>=3600:
                last_hourly=time.time()
                bal_msg,_,_=format_balance_msg()
                send_telegram(f"[{thai_time()}] {price:.2f}$ {bal_msg} BB_L {last['BB_L']:.2f} K {last['K']:.1f} RSI {last['RSI']:.1f}")
            if is_funding_time()[0] or is_news_time()[0]:
                time.sleep(60)
                continue
            if positions and check_max_hold():
                close_position(price, "MAX_HOLD")
                continue
            if len(positions)==0 and buy:
                ok,qty=mexc_buy(price,1)
                if ok: positions.append({'entry':price,'qty':qty,'side':'long','time':time.time()}); send_telegram(f"🟢 LONG ไม้1 @ {price:.2f}")
            if len(positions)==0 and short:
                ok,qty=mexc_sell(price,1)
                if ok: positions.append({'entry':price,'qty':-qty,'side':'short','time':time.time()}); send_telegram(f"🔴 SHORT ไม้1 @ {price:.2f}")
            if positions:
                avg=sum(p['entry']*abs(p['qty']) for p in positions)/sum(abs(p['qty']) for p in positions)
                is_short=any(p.get('qty',0)<0 for p in positions)
                pnl=(avg-price)/avg*100 if is_short else (price-avg)/avg*100
                if pnl>=1.2 and last['K']>78: close_position(price, "FAST")
            time.sleep(60)
        except Exception as e:
            print(e)
            time.sleep(30)
threading.Thread(target=trading_loop, daemon=True).start()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
