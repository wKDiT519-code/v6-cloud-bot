import requests, pandas as pd, os, time, threading, ccxt
from flask import Flask
from datetime import datetime
import pytz

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

SYMBOL = "XAUTUSDT"
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
last_near_alert = 0
last_hourly_report_minute = -1

mexc_public = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
mexc = ccxt.mexc({'apiKey': MEXC_API_KEY, 'secret': MEXC_API_SECRET, 'enableRateLimit': True, 'options': {'defaultType': 'swap'}}) if MEXC_API_KEY and MEXC_API_SECRET else None

def thai_time():
    return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")

def thai_now():
    return datetime.now(pytz.timezone('Asia/Bangkok'))

def is_funding_time():
    try:
        utc_now = datetime.now(pytz.timezone('UTC'))
        utc_hour = utc_now.hour
        utc_min = utc_now.minute
        for fh in FUNDING_TIMES_UTC:
            if utc_hour == fh and utc_min >= (60 - AVOID_FUNDING_MINUTES):
                return True, f"ใกล้ Funding {fh}:00 UTC"
            if utc_hour == fh and utc_min <= AVOID_FUNDING_MINUTES:
                return True, f"หลัง Funding {fh}:00 UTC"
            if fh == 0 and utc_hour == 23 and utc_min >= 55:
                return True, "ใกล้ Funding 00:00 UTC"
        return False, ""
    except:
        return False, ""

def is_news_time():
    if not NEWS_BLACKOUT:
        return False, ""
    try:
        now_th = thai_now()
        if now_th.weekday() == 4 and 1 <= now_th.day <= 7:
            if 19 <= now_th.hour <= 20:
                return True, f"Non-Farm Payroll ศุกร์แรก {now_th.strftime('%d/%m')} 19:30 ICT"
        return False, ""
    except:
        return False, ""

def check_max_hold():
    if not positions:
        return False
    try:
        ft = positions[0].get('time',0)
        return (time.time()-ft)/3600 >= MAX_HOLD_HOURS if ft else False
    except:
        return False

@app.route('/')
def home():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    cm = f" | Cooldown {(cooldown_until-time.time())/3600:.1f}h" if cooldown_until>time.time() else ""
    return f"V100 XAUTUSDT FINAL FIXED-HOURLY LIVE Trades:{stats['total_trades']} WR:{wr:.1f}%{cm}"

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-20:]])
    return f"<h2>V100 XAUT HOURLY-FIX COOLDOWN {COOLDOWN_HOURS}h</h2>Trades:{stats['total_trades']} WR:{wr:.1f}% Total:{stats['total_pnl_pct']:+.2f}%<br><hr>{hist}"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
        print(f"TG sent {r.status_code}")
        return r.status_code==200
    except Exception as e:
        print(f"TG err {e}")
        return False

def get_price_mexc():
    try:
        t = mexc_public.fetch_ticker(SYMBOL)
        return float(t.get('last') or t.get('close'))
    except:
        try:
            r = requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol": "XAUT_USDT"}, timeout=10)
            return float(r.json()['data']['lastPrice'])
        except:
            return None

def get_klines_mexc(limit=150):
    try:
        ohlcv = mexc_public.fetch_ohlcv(SYMBOL, timeframe='15m', limit=limit)
        return pd.DataFrame(ohlcv, columns=['ot','o','h','l','c','v'])
    except Exception as e:
        print(f"MEXC Kline err {e}")
        return None

def calc(df):
    df['BB_mid']=df['c'].rolling(20).mean()
    df['BB_std']=df['c'].rolling(20).std()
    df['BB_U']=df['BB_mid']+2*df['BB_std']
    df['BB_L']=df['BB_mid']-2*df['BB_std']
    low_min=df['l'].rolling(14).min()
    high_max=df['h'].rolling(14).max()
    df['K']=100*(df['c']-low_min)/(high_max-low_min)
    df['D']=df['K'].rolling(3).mean()
    delta=df['c'].diff()
    gain=delta.where(delta>0,0).rolling(14).mean()
    loss=-delta.where(delta<0,0).rolling(14).mean()
    df['RSI']=100-(100/(1+gain/loss))
    return df.dropna()

def check_divergence(df, lookback=20):
    if len(df) < lookback*2: return False, False, "no data"
    try:
        recent = df.iloc[-lookback:]
        prev = df.iloc[-lookback*2:-lookback]
        r_low_idx = recent['l'].idxmin()
        p_low_idx = prev['l'].idxmin()
        bull_div = (recent.loc[r_low_idx,'l'] < prev.loc[p_low_idx,'l']*0.998) and (recent.loc[r_low_idx,'RSI'] > prev.loc[p_low_idx,'RSI']+2) and (recent.loc[r_low_idx,'RSI'] < 50)
        r_high_idx = recent['h'].idxmax()
        p_high_idx = prev['h'].idxmax()
        bear_div = (recent.loc[r_high_idx,'h'] > prev.loc[p_high_idx,'h']*1.002) and (recent.loc[r_high_idx,'RSI'] < prev.loc[p_high_idx,'RSI']-2) and (recent.loc[r_high_idx,'RSI'] > 50)
        return bull_div, bear_div, ""
    except:
        return False, False, ""

def get_balance():
    if not mexc:
        return 10.29, 10.29, 0.0
    try:
        bal=mexc.fetch_balance()
        total = float(bal.get('USDT',{}).get('total') or bal.get('total',{}).get('USDT') or 0)
        free = float(bal.get('USDT',{}).get('free') or 0)
        upnl = 0
        try:
            poss = mexc.fetch_positions([SYMBOL])
            for p in poss:
                upnl += float(p.get('unrealizedPnl') or p.get('unrealisedPnl') or 0)
        except:
            pass
        return total, free, upnl
    except Exception as e:
        print(f"Balance err {e}")
        return 10.29, 10.29, 0.0

def format_balance_msg():
    total, free, upnl = get_balance()
    wr=(stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"💰 คงเหลือ {total:.2f}$ | กำไร/ขาดทุน {upnl:+.2f}$ | สะสม {stats['total_pnl_pct']:+.2f}% WR {wr:.1f}%", {"total":total,"free":free,"upnl":upnl}, wr

def mexc_buy(price,factor):
    if not mexc:
        return True,1
    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL, {'marginMode':'ISOLATED'})
        except: pass
        total,free,upnl=get_balance()
        bal = total if total>0 else free
        if bal<1: bal=10.29
        qty=round(max(bal*(ORDER_PERCENT/100)*factor,5)/price,4)
        mexc.create_market_buy_order(SYMBOL, max(qty,0.001))
        return True,qty
    except Exception as e:
        send_telegram(f"❌ BUY Fail {e}")
        return False,0

def mexc_sell(price,factor):
    if not mexc:
        return True,1
    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL, {'marginMode':'ISOLATED'})
        except: pass
        total,free,upnl=get_balance()
        bal = total if total>0 else free
        if bal<1: bal=10.29
        qty=round(max(bal*(ORDER_PERCENT/100)*factor,5)/price,4)
        mexc.create_market_sell_order(SYMBOL, max(qty,0.001))
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

def should_send_hourly():
    global last_hourly, last_hourly_report_minute
    now = thai_now()
    # V100 FIX: ส่งทุกชั่วโมงตรง 00 นาที + กันส่งซ้ำ + กันกรณีรัน 18:42 แล้วต้องรอถึง 19:42 ถึงจะส่ง
    # ส่งเมื่อ: 1) ครบ 60 นาที หรือ 2) เข้า 00 นาทีของชั่วโมงใหม่ (เช่น 19:00)
    time_ok = (time.time() - last_hourly) >= 3600
    hour_top = (now.minute == 0 and now.second < 30 and last_hourly_report_minute != now.hour)
    return time_ok or hour_top

def trading_loop():
    global positions,highest,lowest,last_hourly,cooldown_until,last_near_alert, last_hourly_report_minute
    print("=== V100 START ===")
    time.sleep(3)
    bal_msg,_,_=format_balance_msg()
    last_hourly = time.time()
    last_hourly_report_minute = thai_now().hour
    ok = send_telegram(f"🚀 [{thai_time()}] V100 XAUT FINAL FIXED-HOURLY LIVE เริ่มแล้ว SYMBOL={SYMBOL} ไม้1=10% ไม้2=20% ไม้3=40%\n{bal_msg}\n⏰ จะรายงานทุกชั่วโมงตรง (xx:00)")
    print(f"Startup TG ok={ok}")
    
    # ส่งรายงานแรกทันทีหลัง 10 วิ เพื่อยืนยัน hourly ทำงาน
    time.sleep(10)
    try:
        price = get_price_mexc()
        total, free, upnl = get_balance()
        if price:
            send_telegram(f"⏰ [{thai_time()}] {SYMBOL} {price:.2f}$\nว่าง - ไม่มีไม้ค้าง\n💰 คงเหลือ {total:.2f}$ | กำไร/ขาดทุน {upnl:+.2f}$\n✅ ระบบรายงานทุกชั่วโมงทำงานแล้ว จะรายงานอีกทีตอน {thai_now().hour+1}:00")
            last_hourly = time.time()
    except Exception as e:
        print(f"First report err {e}")

    while True:
        try:
            price=get_price_mexc()
            df=get_klines_mexc()
            if not price or df is None or len(df)<60:
                # ถึงไม่มีราคา ก็ยังต้องเช็ครายงานชั่วโมง
                if should_send_hourly():
                    try:
                        total, free, upnl = get_balance()
                        p_txt = f"{price:.2f}$" if price else "N/A"
                        msg = f"⏰ [{thai_time()}] {SYMBOL} {p_txt}\nว่าง - ไม่มีไม้ค้าง\n💰 คงเหลือ {total:.2f}$ | กำไร/ขาดทุน {upnl:+.2f}$"
                        send_telegram(msg)
                        last_hourly = time.time()
                        last_hourly_report_minute = thai_now().hour
                    except:
                        pass
                time.sleep(10)
                continue
            
            df=calc(df)
            last=df.iloc[-1]
            prev=df.iloc[-2]
            
            bb_low_touch = any(df['c'].iloc[-3:].values <= df['BB_L'].iloc[-3:].values*1.005)
            bb_high_touch = any(df['c'].iloc[-3:].values >= df['BB_U'].iloc[-3:].values*0.995)
            
            buy=bb_low_touch and prev['K']<prev['D'] and last['K']>last['D'] and last['K']<50 and last['RSI']<50
            short=bb_high_touch and prev['K']>prev['D'] and last['K']<last['D'] and last['K']>50 and last['RSI']>50
            
            bull_div, bear_div, _ = check_divergence(df, 20)

            near_buy = bb_low_touch and last['K'] < 60 and last['RSI'] < 55 and not buy
            near_short = bb_high_touch and last['K'] > 40 and last['RSI'] > 45 and not short
            if (near_buy or near_short) and time.time() - last_near_alert > 900:
                last_near_alert = time.time()
                side = "LONG" if near_buy else "SHORT"
                send_telegram(f"👀 [{thai_time()}] เกือบเข้า {side} {SYMBOL} {price:.2f}$ K {last['K']:.1f} RSI {last['RSI']:.1f} BB_L {last['BB_L']:.2f} BB_U {last['BB_U']:.2f}\nรอครอส Sto")

            if cooldown_until > time.time():
                time.sleep(15)
                continue

            # === V100 FIX HOURLY ===
            if should_send_hourly():
                last_hourly=time.time()
                last_hourly_report_minute = thai_now().hour
                total, free, upnl = get_balance()
                pnl_txt = f"{upnl:+.2f}$"
                pos_count = len(positions)
                if pos_count == 0:
                    status = "ว่าง - ไม่มีไม้ค้าง"
                else:
                    avg=sum(p['entry']*abs(p['qty']) for p in positions)/sum(abs(p['qty']) for p in positions)
                    is_short=any(p.get('qty',0)<0 for p in positions)
                    pnl=(avg-price)/avg*100 if is_short else (price-avg)/avg*100
                    side = "SHORT" if is_short else "LONG"
                    status = f"ถือ {pos_count}ไม้ {side} PnL {pnl:+.2f}%"
                msg = f"⏰ [{thai_time()}] {SYMBOL} {price:.2f}$\n{status}\n💰 คงเหลือ {total:.2f}$ | กำไร/ขาดทุน {pnl_txt}"
                print(f"Hourly report: {msg}")
                send_telegram(msg)

            if positions and check_max_hold():
                close_position(price, f"MAX_HOLD_{MAX_HOLD_HOURS}H")
                continue

            if len(positions)==0 and buy:
                label = "STRONG+DIV" if bull_div else "V100"
                ok, qty = mexc_buy(price, 1)
                if ok:
                    positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
                    highest=price
                    lowest=price
                    total, free, upnl = get_balance()
                    tp = price * (1 + TP_PCT/100)
                    sl = price * (1 - SL_PCT/100)
                    pnl_txt = f"{upnl:+.2f}$" if upnl!=0 else "รอเปิด"
                    msg = f"🟢 LONG ไม้1 {SYMBOL} {price:.2f}$\nTP {tp:.2f} SL {sl:.2f}\n{label}\n💰 คงเหลือ {total:.2f}$ | กำไร/ขาดทุน {pnl_txt}\n📅 {thai_time()}"
                    send_telegram(msg)

            if len(positions)==0 and short:
                label = "STRONG+DIV" if bear_div else "V100"
                ok, qty = mexc_sell(price, 1)
                if ok:
                    positions.append({'entry': price, 'qty': -qty, 'side': 'short', 'time': time.time()})
                    highest=price
                    lowest=price
                    total, free, upnl = get_balance()
                    tp = price * (1 - TP_PCT/100)
                    sl = price * (1 + SL_PCT/100)
                    pnl_txt = f"{upnl:+.2f}$" if upnl!=0 else "รอเปิด"
                    msg = f"🔴 SHORT ไม้1 {SYMBOL} {price:.2f}$\nTP {tp:.2f} SL {sl:.2f}\n{label}\n💰 คงเหลือ {total:.2f}$ | กำไร/ขาดทุน {pnl_txt}\n📅 {thai_time()}"
                    send_telegram(msg)

            if positions:
                is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
                total_qty = sum(abs(p['qty']) for p in positions)
                avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
                if price>highest: highest=price
                if price<lowest: lowest=price
                sell_fast = last['K'] > 78 or last['RSI'] > 75
                if pnl>=1.2 and sell_fast:
                    close_position(price, "FAST")
                elif not is_short and pnl>=1.5 and price <= highest*0.992:
                    close_position(price, "TRAILING")
                elif is_short and pnl>=1.5 and price >= lowest*1.008:
                    close_position(price, "TRAILING_SHORT")
                elif not is_short and price <= positions[-1]['entry']*0.988 and len(positions)<MAX_WOODS and buy:
                    factor=2**len(positions)
                    ok, qty = mexc_buy(price, factor)
                    if ok:
                        positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
                        total,_,_=get_balance()
                        send_telegram(f"🔧 แก้ LONG ไม้{len(positions)} @ {price:.2f} คงเหลือ {total:.2f}$")
                elif is_short and price >= positions[-1]['entry']*1.012 and len(positions)<MAX_WOODS and short:
                    factor=2**len(positions)
                    ok, qty = mexc_sell(price, factor)
                    if ok:
                        positions.append({'entry': price, 'qty': -qty, 'side': 'short', 'time': time.time()})
                        total,_,_=get_balance()
                        send_telegram(f"🔧 แก้ SHORT ไม้{len(positions)} @ {price:.2f} คงเหลือ {total:.2f}$")

            time.sleep(15)
        except Exception as e:
            print(f"Loop err {e}")
            import traceback
            traceback.print_exc()
            time.sleep(15)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
