import requests, pandas as pd, os, time, threading, ccxt
from flask import Flask
from datetime import datetime
import pytz

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

SYMBOL = "PAXGUSDT"
RENDER_URL = "https://v95-cloud-bot-1.onrender.com"
INTERVAL = "15m"
ORDER_PERCENT = 10
LEVERAGE = 3
TP_PCT = 3.2
SL_PCT = 1.8
USE_EXCHANGE_SLTP = True
CANCEL_OLD_SLTP = True
MAX_WOODS = 3
COOLDOWN_HOURS = 4
MAX_HOLD_HOURS = 96
AVOID_FUNDING_MINUTES = 5
FUNDING_TIMES_UTC = [0, 8, 16]
NEWS_BLACKOUT = True

app = Flask(__name__)
positions, highest, lowest, last_hourly = [], 0, 999999, 0
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"best_trade":0.0,"worst_trade":0.0,"history":[]}
cooldown_until = 0
consecutive_full_losses = 0

mexc_public = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
mexc = ccxt.mexc({'apiKey': MEXC_API_KEY, 'secret': MEXC_API_SECRET, 'enableRateLimit': True, 'options': {'defaultType': 'swap'}}) if MEXC_API_KEY and MEXC_API_SECRET else None

def thai_time():
    return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")

def is_funding_time():
    try:
        utc_hour = datetime.now(pytz.timezone('UTC')).hour
        utc_min = datetime.now(pytz.timezone('UTC')).minute
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
        now_th = datetime.now(pytz.timezone('Asia/Bangkok'))
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
        first_entry_time = positions[0].get('time', 0)
        if first_entry_time == 0:
            return False
        held_hours = (time.time() - first_entry_time) / 3600
        if held_hours >= MAX_HOLD_HOURS:
            return True
        return False
    except:
        return False

@app.route('/')
def home():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    cool_msg = ""
    if cooldown_until > time.time():
        remain = (cooldown_until - time.time())/3600
        cool_msg = f" | ⏸️ Cooldown {remain:.1f}h"
    return f"V95 FINAL GOLD BB+STO+RSI+DIV LONG+SHORT SLTP BALANCE COOLDOWN FUNDING/NFP LIVE | Trades:{stats['total_trades']} WR:{wr:.1f}%{cool_msg}"

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-20:]])
    cool = ""
    if cooldown_until > time.time():
        cool = f"<br>⏸️ หยุดเทรดถึง {datetime.fromtimestamp(cooldown_until).strftime('%H:%M')} เหลือ {(cooldown_until-time.time())/3600:.1f} ชม."
    return f"<h2>V95 FINAL + FUNDING/NFP + COOLDOWN {COOLDOWN_HOURS}h MAX_HOLD {MAX_HOLD_HOURS}h</h2>Trades:{stats['total_trades']} WR:{wr:.1f}% Total:{stats['total_pnl_pct']:+.2f}%{cool}<br><hr>{hist}"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print(f"TG err {e}")

def get_price_mexc():
    try:
        t = mexc_public.fetch_ticker(SYMBOL)
        return float(t.get('last') or t.get('close'))
    except:
        try:
            r = requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol": "PAXG_USDT"}, timeout=10)
            return float(r.json()['data']['lastPrice'])
        except:
            return None

def get_klines_mexc(limit=150):
    try:
        ohlcv = mexc_public.fetch_ohlcv(SYMBOL, timeframe='15m', limit=limit)
        return pd.DataFrame(ohlcv, columns=['ot','o','h','l','c','v'])
    except Exception as e:
        print(f"Kline err {e}")
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
        bull_div = (recent.loc[r_low_idx,'l'] < prev.loc[p_low_idx,'l']*0.998) and (recent.loc[r_low_idx,'RSI'] > prev.loc[p_low_idx,'RSI']+2) and (recent.loc[r_low_idx,'RSI'] < 48)
        r_high_idx = recent['h'].idxmax()
        p_high_idx = prev['h'].idxmax()
        bear_div = (recent.loc[r_high_idx,'h'] > prev.loc[p_high_idx,'h']*1.002) and (recent.loc[r_high_idx,'RSI'] < prev.loc[p_high_idx,'RSI']-2) and (recent.loc[r_high_idx,'RSI'] > 52)
        detail = f"LL {prev.loc[p_low_idx,'l']:.1f}->{recent.loc[r_low_idx,'l']:.1f} RSI {prev.loc[p_low_idx,'RSI']:.1f}->{recent.loc[r_low_idx,'RSI']:.1f} | HH {prev.loc[p_high_idx,'h']:.1f}->{recent.loc[r_high_idx,'h']:.1f} RSI {prev.loc[p_high_idx,'RSI']:.1f}->{recent.loc[r_high_idx,'RSI']:.1f}"
        return bull_div, bear_div, detail
    except:
        return False, False, "err"

def get_balance():
    if not mexc: return 0
    try:
        bal = mexc.fetch_balance()
        return float(bal.get('USDT',{}).get('total') or bal.get('total',{}).get('USDT') or 0)
    except:
        return 0

def get_balance_detail():
    if not mexc: return {"total":0,"free":0,"upnl":0,"equity":0}
    try:
        bal = mexc.fetch_balance()
        total = float(bal.get('USDT',{}).get('total') or bal.get('total',{}).get('USDT') or 0)
        free = float(bal.get('USDT',{}).get('free') or 0)
        upnl = 0
        try:
            poss = mexc.fetch_positions([SYMBOL])
            for p in poss:
                upnl += float(p.get('unrealizedPnl',0) or p.get('unrealizedPNL',0) or 0)
        except:
            pass
        return {"total": total, "free": free, "upnl": upnl, "equity": total}
    except:
        return {"total":0,"free":0,"upnl":0,"equity":0}

def format_balance_msg():
    d = get_balance_detail()
    total_pnl = stats["total_pnl_pct"]
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"💰 พอร์ต ${d['total']:.2f} | ฟรี ${d['free']:.2f} | Unrealized {d['upnl']:+.2f}$ | สะสม {total_pnl:+.2f}% | WR {wr:.1f}% | Trades {stats['total_trades']}", d, wr

def cancel_all_sltp():
    if not mexc or not CANCEL_OLD_SLTP: return
    try:
        orders = mexc.fetch_open_orders(SYMBOL)
        for o in orders:
            if 'stop' in str(o.get('type','')).lower() or 'take' in str(o.get('type','')).lower() or o.get('stopPrice') or o.get('triggerPrice'):
                try: mexc.cancel_order(o['id'], SYMBOL)
                except: pass
    except Exception as e:
        print(f"Cancel err {e}")

def set_sl_tp_long(qty, entry):
    if not mexc or not USE_EXCHANGE_SLTP: return
    sl = 3980.0 if 4040 < entry < 4060 else round(entry*(1-SL_PCT/100),2)
    tp = 4180.0 if 4040 < entry < 4060 else round(entry*(1+TP_PCT/100),2)
    try:
        mexc.create_order(SYMBOL, 'market', 'sell', qty, None, {'stopPrice': sl, 'reduceOnly': True, 'type': 'stop_market'})
        send_telegram(f"🛡️ [{thai_time()}] SL LONG {sl:.2f} Qty {qty}")
    except:
        try: mexc.create_order(SYMBOL, 'market', 'sell', qty, None, {'triggerPrice': sl, 'reduceOnly': True, 'type': 'stop'})
        except: pass
    try:
        mexc.create_order(SYMBOL, 'limit', 'sell', qty, tp, {'reduceOnly': True})
        send_telegram(f"🎯 [{thai_time()}] TP LONG {tp:.2f} Qty {qty}")
    except: pass

def set_sl_tp_short(qty, entry):
    if not mexc or not USE_EXCHANGE_SLTP: return
    sl = 4260.0 if 4170 < entry < 4190 else round(entry*(1+SL_PCT/100),2)
    tp = 4050.0 if 4170 < entry < 4190 else round(entry*(1-TP_PCT/100),2)
    try:
        mexc.create_order(SYMBOL, 'market', 'buy', qty, None, {'stopPrice': sl, 'reduceOnly': True, 'type': 'stop_market'})
        send_telegram(f"🛡️ [{thai_time()}] SL SHORT {sl:.2f} Qty {qty}")
    except:
        try: mexc.create_order(SYMBOL, 'market', 'buy', qty, None, {'triggerPrice': sl, 'reduceOnly': True, 'type': 'stop'})
        except: pass
    try:
        mexc.create_order(SYMBOL, 'limit', 'buy', qty, tp, {'reduceOnly': True})
        send_telegram(f"🎯 [{thai_time()}] TP SHORT {tp:.2f} Qty {qty}")
    except: pass

def mexc_buy(price, factor):
    if not mexc:
        bal_msg, _, _ = format_balance_msg()
        send_telegram(f"[{thai_time()}] จำลอง BUY ไม้{factor} {ORDER_PERCENT*factor}% @ {price:.2f}\n{bal_msg}")
        return True, 1
    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL, {'marginMode': 'ISOLATED'})
        except: pass
        bal = get_balance()
        usdt = bal*(ORDER_PERCENT/100)*factor
        if usdt < 5: usdt = 5
        qty = round(usdt/price, 4)
        if qty < 0.001: qty = 0.001
        if CANCEL_OLD_SLTP and factor==1: cancel_all_sltp()
        mexc.create_market_buy_order(SYMBOL, qty)
        bal_msg, _, _ = format_balance_msg()
        send_telegram(f"✅ [{thai_time()}] BUY ไม้{factor} {ORDER_PERCENT*factor}% @ {price:.2f} Qty {qty}\n{bal_msg}")
        set_sl_tp_long(qty, price)
        return True, qty
    except Exception as e:
        send_telegram(f"❌ [{thai_time()}] BUY ไม้{factor} Fail {str(e)[:200]}")
        return False, 0

def mexc_sell(price, factor):
    if not mexc:
        bal_msg, _, _ = format_balance_msg()
        send_telegram(f"[{thai_time()}] จำลอง SHORT ไม้{factor} {ORDER_PERCENT*factor}% @ {price:.2f}\n{bal_msg}")
        return True, 1
    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL, {'marginMode': 'ISOLATED'})
        except: pass
        bal = get_balance()
        usdt = bal*(ORDER_PERCENT/100)*factor
        if usdt < 5: usdt = 5
        qty = round(usdt/price, 4)
        if qty < 0.001: qty = 0.001
        if CANCEL_OLD_SLTP and factor==1: cancel_all_sltp()
        mexc.create_market_sell_order(SYMBOL, qty)
        bal_msg, _, _ = format_balance_msg()
        send_telegram(f"✅ [{thai_time()}] SHORT ไม้{factor} {ORDER_PERCENT*factor}% @ {price:.2f} Qty {qty}\n{bal_msg}")
        set_sl_tp_short(qty, price)
        return True, qty
    except Exception as e:
        send_telegram(f"❌ [{thai_time()}] SHORT ไม้{factor} Fail {str(e)[:200]}")
        return False, 0

def mexc_close(side='long'):
    if not mexc: return True
    try:
        if side=='short':
            mexc.create_market_buy_order(SYMBOL, None, {'closePosition': True})
        else:
            mexc.create_market_sell_order(SYMBOL, None, {'closePosition': True})
        return True
    except:
        try:
            poss=mexc.fetch_positions([SYMBOL])
            for p in poss:
                c=float(p.get('contracts',0) or 0)
                if c!=0:
                    mexc.create_market_order(SYMBOL, 'sell' if c>0 else 'buy', abs(c), None, {'reduceOnly': True})
            return True
        except Exception as e:
            send_telegram(f"❌ CLOSE Fail {str(e)[:150]}")
            return False

def close_position(price, typ):
    global positions, stats, cooldown_until, consecutive_full_losses
    if not positions: return
    total_qty = sum(abs(p['qty']) for p in positions)
    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty if total_qty else positions[0]['entry']
    is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
    pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
    num_woods = len(positions)
    stats["total_trades"]+=1
    stats["total_pnl_pct"]+=pnl
    if pnl>0: 
        stats["wins"]+=1
        consecutive_full_losses = 0
    else: 
        stats["losses"]+=1
        if num_woods >= MAX_WOODS:
            consecutive_full_losses += 1
    stats["history"].append({"time": thai_time(), "type": typ, "entry": avg, "exit": price, "pnl": pnl})
    cancel_all_sltp()
    mexc_close('short' if is_short else 'long')
    bal_msg, _, wr = format_balance_msg()
    if pnl < 0 and num_woods >= MAX_WOODS:
        cooldown_until = time.time() + COOLDOWN_HOURS*3600
        send_telegram(f"✅ [{thai_time()}] {typ} ปิดขาดทุน {pnl:+.2f}% ครบ {num_woods}ไม้ {avg:.2f}->{price:.2f} WR {wr:.1f}%\n{bal_msg}\n⏸️ แก้ครบ {MAX_WOODS}ไม้แล้วยังขาดทุน - หยุดเทรด {COOLDOWN_HOURS} ชม.")
    else:
        send_telegram(f"✅ [{thai_time()}] {typ} ปิด {pnl:+.2f}% {avg:.2f}->{price:.2f} WR {wr:.1f}% (ใช้ {num_woods}ไม้)\n{bal_msg}")
    positions.clear()

def trading_loop():
    global positions, highest, lowest, last_hourly, cooldown_until
    time.sleep(5)
    bal_msg, _, _ = format_balance_msg()
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
            bb_low_touch = any(df['c'].iloc[-3:].values <= df['BB_L'].iloc[-3:].values*1.0015)
            bb_high_touch = any(df['c'].iloc[-3:].values >= df['BB_U'].iloc[-3:].values*0.9985)
            sto_cross_up = prev['K'] < prev['D'] and last['K'] > last['D'] and last['K'] < 40
            sto_cross_down = prev['K'] > prev['D'] and last['K'] < last['D'] and last['K'] > 60
            sto_oversold = last['K'] < 25
            sto_overbought = last['K'] > 75
            rsi_oversold = last['RSI'] < 42 and last['RSI'] > prev['RSI']
            rsi_overbought = last['RSI'] > 58 and last['RSI'] < prev['RSI']
            bull_div, bear_div, div_detail = check_divergence(df, 20)
            buy_3cond = bb_low_touch and (sto_cross_up or sto_oversold) and rsi_oversold
            short_3cond = bb_high_touch and (sto_cross_down or sto_overbought) and rsi_overbought
            buy_strong = buy_3cond and bull_div
            short_strong = short_3cond and bear_div
            sell_fast = last['K'] > 78 or last['RSI'] > 75

            if cooldown_until > time.time():
                if time.time()-last_hourly >= 3600:
                    last_hourly=time.time()
                    remain = (cooldown_until - time.time())/3600
                    bal_msg, _, _ = format_balance_msg()
                    send_telegram(f"⏸️ [{thai_time()}] หยุดเทรด {remain:.1f}ชม. | {bal_msg}")
                time.sleep(60)
                continue

            if time.time()-last_hourly >= 3600:
                last_hourly=time.time()
                bal_msg, bal_detail, wr = format_balance_msg()
                if len(positions)==0:
                    status = bal_msg
                else:
                    is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
                    total_qty = sum(abs(p['qty']) for p in positions)
                    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                    pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
                    status = f"ถือ {len(positions)}ไม้ PnL {pnl:+.2f}% ({bal_detail['upnl']:+.2f}$) | {bal_msg}"
                send_telegram(f"[{thai_time()}] {price:.2f}$ {status} BB_L {last['BB_L']:.2f} BB_U {last['BB_U']:.2f} K {last['K']:.1f} RSI {last['RSI']:.1f} BULL={bull_div} BEAR={bear_div}\n{div_detail}")

            funding_block, funding_reason = is_funding_time()
            news_block, news_reason = is_news_time()

            if positions and check_max_hold():
                bal_msg, _, _ = format_balance_msg()
                send_telegram(f"⏰ [{thai_time()}] บังคับปิด ถือเกิน {MAX_HOLD_HOURS}ชม. ป้องกัน Funding+ข่าว\n{bal_msg}")
                close_position(price, f"MAX_HOLD_{MAX_HOLD_HOURS}H")
                continue

            if funding_block or news_block:
                if time.time()-last_hourly >= 3600:
                    reason = funding_reason or news_reason
                    bal_msg, _, _ = format_balance_msg()
                    send_telegram(f"⚠️ [{thai_time()}] งดเข้า: {reason}\n{bal_msg}")
                    last_hourly = time.time()
                if positions:
                    is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
                    total_qty = sum(abs(p['qty']) for p in positions)
                    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                    pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
                    if pnl > 0.5:
                        send_telegram(f"🛡️ [{thai_time()}] ปิดก่อนข่าว {funding_reason or news_reason} กำไร {pnl:.2f}%")
                        close_position(price, "PRE_NEWS_PROFIT")
                time.sleep(60)
                continue

            if len(positions)==0 and buy_3cond:
                label = "STRONG+DIV" if buy_strong else "3COND"
                ok, qty = mexc_buy(price, 1)
                if ok:
                    positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
                    highest=price
                    lowest=price
                    bal_msg, _, _ = format_balance_msg()
                    send_telegram(f"🟢 [{thai_time()}] LONG ไม้1 {label} 10% {price:.2f}$ SL {price*(1-SL_PCT/100):.2f} TP {price*(1+TP_PCT/100):.2f} DIV={bull_div}\n{bal_msg}\n{div_detail}")

            if len(positions)==0 and short_3cond:
                label = "STRONG+DIV" if short_strong else "3COND"
                ok, qty = mexc_sell(price, 1)
                if ok:
                    positions.append({'entry': price, 'qty': -qty, 'side': 'short', 'time': time.time()})
                    highest=price
                    lowest=price
                    bal_msg, _, _ = format_balance_msg()
                    send_telegram(f"🔴 [{thai_time()}] SHORT ไม้1 {label} 10% {price:.2f}$ SL {price*(1+SL_PCT/100):.2f} TP {price*(1-TP_PCT/100):.2f} DIV={bear_div}\n{bal_msg}\n{div_detail}")

            if positions:
                is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
                total_qty = sum(abs(p['qty']) for p in positions)
                avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
                if price>highest: highest=price
                if price<lowest: lowest=price
                if pnl>=1.2 and sell_fast:
                    close_position(price, "FAST")
                elif not is_short and pnl>=1.5 and price <= highest*0.992:
                    close_position(price, "TRAILING")
                elif is_short and pnl>=1.5 and price >= lowest*1.008:
                    close_position(price, "TRAILING_SHORT")
                elif not is_short and price <= positions[-1]['entry']*0.988 and len(positions)<MAX_WOODS and buy_3cond:
                    factor=2**len(positions)
                    ok, qty = mexc_buy(price, factor)
                    if ok:
                        positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
                        bal_msg, _, _ = format_balance_msg()
                        send_telegram(f"🔧 แก้ LONG ไม้{len(positions)} {10*factor if factor<=4 else factor*10}% @ {price:.2f} SL {price*(1-SL_PCT/100):.2f}\n{bal_msg}")
                elif is_short and price >= positions[-1]['entry']*1.012 and len(positions)<MAX_WOODS and short_3cond:
                    factor=2**len(positions)
                    ok, qty = mexc_sell(price, factor)
                    if ok:
                        positions.append({'entry': price, 'qty': -qty, 'side': 'short', 'time': time.time()})
                        bal_msg, _, _ = format_balance_msg()
                        send_telegram(f"🔧 แก้ SHORT ไม้{len(positions)} {10*factor if factor<=4 else factor*10}% @ {price:.2f} SL {price*(1+SL_PCT/100):.2f}\n{bal_msg}")

            time.sleep(60)
        except Exception as e:
            print(f"Loop err {e}")
            time.sleep(30)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
