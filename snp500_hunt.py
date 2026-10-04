import yfinance as yf
import pandas as pd
import requests
import io
import smtplib
import time
import numpy as np # 新增 numpy 用於計算標準差
from email.header import Header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path
# ===========================
# 🔧 使用者設定區
# ===========================
# 帳密放在同資料夾的 mail.env（已列入 .gitignore，不會進 git）。格式：
#   EMAIL_SENDER=你的Gmail地址
#   EMAIL_PASSWORD=16 碼應用程式密碼
#   EMAIL_RECEIVER=收件地址（選填，預設寄給自己）
def _load_mail_env():
    p = Path(__file__).with_name("mail.env")
    vals = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals

_MAIL = _load_mail_env()
EMAIL_SENDER = _MAIL.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = _MAIL.get("EMAIL_PASSWORD", "").replace(" ", "")
EMAIL_RECEIVER = _MAIL.get("EMAIL_RECEIVER") or EMAIL_SENDER

# ===========================
# 1. 抓取 S&P 500
# ===========================
def get_sp500_tickers():
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        tables = pd.read_html(io.StringIO(response.text))
        target_df = None
        for df in tables:
            if len(df) > 400 and ('Symbol' in df.columns or 'Ticker' in df.columns):
                target_df = df; break
        if target_df is None: raise ValueError
        col = 'Symbol' if 'Symbol' in target_df.columns else 'Ticker'
        tickers = [str(t).replace('.', '-') for t in target_df[col].tolist()]
        print(f"✅ [系統] 成功取得 {len(tickers)} 檔 S&P 500 代碼")
        return tickers
    except:
        print("⚠️ [警告] Wikipedia 抓取失敗，切換至備用清單")
        return ['NVDA', 'AAPL', 'MSFT', 'AMZN', 'TSLA', 'AMD', 'GOOGL', 'META', 'AVGO', 'JPM']

# ===========================
# 2. 技術指標計算
# ===========================
def check_trend_details(series):
    if len(series) < 200: return False, "資料不足"
    curr = series.iloc[-1]
    ma50 = series.iloc[-50:].mean()
    ma200 = series.iloc[-200:].mean()
    dist_200 = ((curr - ma200) / ma200) * 100
    
    if curr > ma200 and ma50 > ma200: return True, f"📈 多頭 (+{dist_200:.1f}%)"
    elif curr < ma200: return False, f"🐻 空頭 ({dist_200:.1f}%)"
    else: return False, "😐 整理中"

def judge_price_action(open_p, high_p, low_p, close_p):
    body = abs(close_p - open_p)
    total_range = high_p - low_p
    upper_shadow = high_p - max(close_p, open_p)
    lower_shadow = min(close_p, open_p) - low_p
    if total_range == 0: return "盤整"
    if upper_shadow > (body * 1.5) and upper_shadow > lower_shadow: return "⚠️ 高檔遇壓"
    if close_p > open_p and body > (total_range * 0.6) and upper_shadow < (total_range * 0.2): return "🔥 強力進攻"
    if lower_shadow > (body * 2) and lower_shadow > upper_shadow: return "⚓️ 底部支撐"
    return "普通"

def calculate_rs_value(stock_series, spy_series):
    try:
        if len(stock_series) < 60: return 0
        s_ret = (stock_series.iloc[-1] - stock_series.iloc[-60]) / stock_series.iloc[-60]
        m_ret = (spy_series.iloc[-1] - spy_series.iloc[-60]) / spy_series.iloc[-60]
        return s_ret - m_ret
    except: return 0

def calculate_vcp_ratio(close_series):
    try:
        if len(close_series) < 60: return 1.0
        std_short = close_series.iloc[-11:-1].std()
        std_long = close_series.iloc[-61:-1].std()
        if std_long == 0: return 1.0
        return std_short / std_long
    except: return 1.0

# ===========================
# 3. 基本面與籌碼細節 (含列印)
# ===========================
def get_fundamental_details(ticker):
    data = {
        '營收成長': 'N/A', '獲利成長': 'N/A', '本益比': 'N/A',
        '機構持股': 'N/A', '內部人': '無', 'has_insider_buy': False
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        time.sleep(0.2)
        stock = yf.Ticker(ticker)
        info = stock.info
        
        if 'revenueGrowth' in info: data['營收成長'] = f"{info['revenueGrowth']:.1%}"
        if 'earningsGrowth' in info: data['獲利成長'] = f"{info['earningsGrowth']:.1%}"
        if 'forwardPE' in info: data['本益比'] = f"{info['forwardPE']:.1f}"
        if 'heldPercentInstitutions' in info: data['機構持股'] = f"{info['heldPercentInstitutions']:.0%}"

        # --- 修正後的內部人爬蟲邏輯 ---
        fv_url = f"https://finviz.com/quote.ashx?t={ticker}"
        resp = requests.get(fv_url, headers=headers, timeout=5)
        
        if resp.status_code == 200:
            tables = pd.read_html(io.StringIO(resp.text))
            for df in tables:
                # 找到內部人交易表格
                if 'Relationship' in df.columns and 'Transaction' in df.columns:
                    # 1. 只取最近的 10 筆交易 (避免看到太久以前的)
                    recent_df = df.head(10)
                    
                    # 2. 統計買賣次數
                    # Transaction == 'Buy' (公開市場買進)
                    # Transaction == 'Sale' (公開市場賣出)
                    # 注意：Option Exercise (行使選擇權) 通常不視為直接買進訊號，所以不計入
                    buy_count = len(recent_df[recent_df['Transaction'] == 'Buy'])
                    sell_count = len(recent_df[recent_df['Transaction'] == 'Sale'])
                    
                    # 3. 智慧判斷邏輯
                    if buy_count > 0:
                        latest_buy = recent_df[recent_df['Transaction'] == 'Buy'].iloc[0]
                        buy_date = latest_buy['Date']
                        who = latest_buy['Relationship']

                        # 情境 A: 純買進 (沒有賣出) -> 這是好訊號
                        if sell_count == 0:
                            data['內部人'] = f"🔥 {who} 買進 ({buy_date})"
                            data['has_insider_buy'] = True
                        
                        # 情境 B: 買多於賣 (群聚買進) -> 這是強訊號
                        elif buy_count > sell_count:
                            data['內部人'] = f"🔥 群聚買進 ({buy_count}買/{sell_count}賣)"
                            data['has_insider_buy'] = True
                        
                        # 情境 C: 萬綠叢中一點紅 (賣多於買) -> 這是雜訊，忽略！
                        else:
                            data['內部人'] = f"⚠️ 賣壓大 ({buy_count}買/{sell_count}賣)"
                            data['has_insider_buy'] = False # 雖然有買，但不加分
                    
                    # 如果完全沒有買，只有賣
                    elif sell_count > 2:
                         data['內部人'] = f"📉 近期 {sell_count} 筆賣出"
                    
                    break # 找到表格處理完就跳出

    except Exception as e:
        print(f"   ⚠️ 抓取 {ticker} 基本面失敗: {e}")
    
    # 列印出來讓你檢查邏輯對不對
    print(f"   L 營收: {data['營收成長']:<7} | 內部人狀態: {data['內部人']}")
    
    return data

# ===========================
# 4. 核心分析 (含過程列印)
# ===========================
def analyze_market_details():
    tickers = get_sp500_tickers()
    tickers_with_spy = tickers + ['SPY']
    
    print(f"⏳ [系統] 正在下載 1 年歷史數據... (請稍候 1-2 分鐘)")
    try:
        data = yf.download(tickers_with_spy, period="1y", auto_adjust=False, group_by='ticker', progress=False)
        valid_cols = data.columns.get_level_values(0).unique()
        print(f"📊 [系統] 下載完成，有效股票數: {len(valid_cols)} 檔")
    except: return pd.DataFrame()

    if 'SPY' in valid_cols: spy_data = data['SPY']['Close']
    else: spy_data = pd.Series([0]*100)

    all_dates = data.index
    target_dates = all_dates[-7:]
    stock_summary = {}
    
    print(f"🔍 [系統] 開始掃描過去 7 個交易日 ({target_dates[0].date()} ~ {target_dates[-1].date()})")

    for target_date in target_dates:
        try: idx = data.index.get_loc(target_date)
        except: continue
        if idx < 200: continue
        
        date_str = target_date.strftime('%Y-%m-%d')
        
        for stock in tickers:
            try:
                if stock not in valid_cols: continue
                df = data[stock]
                
                # 趨勢濾網
                is_uptrend, trend_str = check_trend_details(df['Close'].iloc[:idx+1])
                if not is_uptrend: continue

                today_C = df['Close'].iloc[idx]
                today_V = df['Volume'].iloc[idx]
                prev_C = df['Close'].iloc[idx-1]
                
                if pd.isna(today_C) or today_V == 0: continue

                pct_change = ((today_C - prev_C) / prev_C) * 100
                avg_vol = df['Volume'].iloc[idx-21:idx].mean()
                rvol = today_V / avg_vol if avg_vol > 0 else 0
                
                # 爆量篩選
                if pct_change > 1.0 and rvol > 1.5:
                    action = judge_price_action(df['Open'].iloc[idx], df['High'].iloc[idx], df['Low'].iloc[idx], today_C)
                    flow_score = pct_change * rvol
                    if "普通" in action and flow_score < 3: continue
                    
                    # === 這裡印出初篩通過的股票 ===
                    print(f"   👉 [{date_str}] {stock:<5} 漲{pct_change:>5.1f}% | RVol {rvol:>4.1f}x | {action}")

                    rs_score = calculate_rs_value(df['Close'].iloc[:idx+1], spy_data.iloc[:idx+1])
                    vcp_ratio = calculate_vcp_ratio(df['Close'].iloc[:idx+1])
                    
                    if stock not in stock_summary:
                        stock_summary[stock] = {
                            '次數': 0, '總強度': 0, '最新型態': action, '最新RVol': rvol,
                            '最後出現日': target_date, '趨勢': trend_str,
                            'RS值': rs_score, 'VCP係數': vcp_ratio
                        }
                    
                    stock_summary[stock]['次數'] += 1
                    stock_summary[stock]['總強度'] += flow_score
                    if rs_score > 0: stock_summary[stock]['總強度'] += 2
                    if vcp_ratio < 0.6: stock_summary[stock]['總強度'] += 5

                    if target_date >= stock_summary[stock]['最後出現日']:
                        stock_summary[stock]['最新型態'] = action; stock_summary[stock]['最新RVol'] = rvol
                        stock_summary[stock]['RS值'] = rs_score; stock_summary[stock]['VCP係數'] = vcp_ratio
                        stock_summary[stock]['最後出現日'] = target_date
            except: continue

    # 最後整理
    final_results = []
    last_valid_date = target_dates[-1]
    candidates = [s for s, i in stock_summary.items() if (last_valid_date - i['最後出現日']).days <= 3]
    
    print(f"\n🕵️ [系統] 進入第二階段：深度分析 {len(candidates)} 檔候選股...")

    for stock in candidates:
        info = stock_summary[stock]
        
        print(f"   🔍 正在調查 {stock} ...")
        # 抓取並列印基本面
        funda = get_fundamental_details(stock)
        
        signal = "👀 觀望"; color = "black"
        if info['VCP係數'] < 0.6 and info['RS值'] > 0.1: signal = "🚀 完美買點"; color = "#8e44ad"
        elif info['次數'] >= 2: signal = "★ 強力買入"; color = "#d35400"
        elif info['最新RVol'] > 2.5: signal = "⚡️ 爆發起漲"; color = "red"
        
        if "高檔" in info['最新型態']: signal = "⚠️ 危險"; color = "gray"
        
        # 使用布林值判斷內部人
        if funda['has_insider_buy']:
            info['總強度'] += 10
            signal += " (內部人買)"
            print(f"      🔥 發現內部人買進訊號！總分加權！")

        final_results.append({
            "股票": stock, "信號": signal, "Color": color,
            "趨勢狀態": info['趨勢'],
            "RS細節": f"{info['RS值']*100:+.1f}%",
            "VCP細節": f"{info['VCP係數']:.2f}",
            "技術數據": f"RVol: {info['最新RVol']:.1f}x<br>{info['最新型態']}",
            "基本面": f"營:{funda['營收成長']}<br>利:{funda['獲利成長']}",
            "籌碼": f"機:{funda['機構持股']}<br><span style='color:#d35400'>{funda['內部人']}</span>",
            "總分": info['總強度']
        })

    if not final_results: return pd.DataFrame()
    
    df_result = pd.DataFrame(final_results).sort_values(by="總分", ascending=False).head(20)
    
    # === 最終結果印在終端機，方便核對 ===
    print("\n" + "="*50)
    print("🏆 [系統] 最終掃描結果 (將寄送 Email)")
    print("="*50)
    # 選擇性印出重要欄位
    print(df_result[['股票', '信號', 'RS細節', 'VCP細節', '總分']].to_string(index=False))
    print("="*50 + "\n")
    
    return df_result

# ===========================
# 5. 發送 Email
# ===========================
def send_email_report():
    df = analyze_market_details()
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    if not df.empty:
        rows = ""
        for _, row in df.iterrows():
            rs_val = float(row['RS細節'].strip('%'))
            rs_style = "color:red" if rs_val > 10 else "color:green" if rs_val > 0 else "color:gray"
            vcp_val = float(row['VCP細節'])
            vcp_style = "background:#d5f5e3;font-weight:bold" if vcp_val < 0.6 else ""

            rows += f"""
            <tr style="border-bottom:1px solid #eee; font-size:13px;">
                <td style="padding:8px;text-align:center"><b>{row['股票']}</b></td>
                <td style="padding:8px;text-align:center;color:{row['Color']}"><b>{row['信號']}</b></td>
                <td style="padding:8px;font-size:11px">{row['趨勢狀態']}</td>
                <td style="padding:8px;text-align:center;{rs_style}">{row['RS細節']}</td>
                <td style="padding:8px;text-align:center;{vcp_style}">{row['VCP細節']}</td>
                <td style="padding:8px;font-size:11px">{row['技術數據']}</td>
                <td style="padding:8px;font-size:11px">{row['基本面']}</td>
                <td style="padding:8px;font-size:11px">{row['籌碼']}</td>
            </tr>"""
            
        table = f"""<table style="width:100%;border-collapse:collapse;background:white;"><thead><tr style="background:#f2f2f2;font-size:12px;"><th style="padding:8px">股票</th><th style="padding:8px">信號</th><th style="padding:8px">趨勢</th><th style="padding:8px">RS</th><th style="padding:8px">VCP</th><th style="padding:8px">技術</th><th style="padding:8px">基本</th><th style="padding:8px">籌碼</th></tr></thead><tbody>{rows}</tbody></table>"""
        status_msg = f"🔥 S&P 500 深度分析報表 (成功掃描 {len(df)} 檔)"
    else:
        table = "<div>今日無信號</div>"; status_msg = "⚠️ 無信號"

    legend = """<div style="margin-top:20px;padding:10px;background:#f8f9fa;border:1px solid #ddd;font-size:12px;"><b>📊 指標說明：</b><br>1. <b>趨勢</b>: 需 > 年線(多頭)。<br>2. <b>RS</b>: >0% 代表強於大盤。<br>3. <b>VCP</b>: <0.6 代表波動壓縮。<br>4. <b>RVol</b>: >1.5 為爆量。</div>"""

    html = f"""<html><body style="font-family:Arial,sans-serif;color:#333;max-width:900px;margin:0 auto"><h2 style="color:#2c3e50">📊 美股狙擊手：數據透明版 ({date_str})</h2><p>{status_msg}</p>{table}{legend}</body></html>"""

    msg = MIMEMultipart()
    msg['Subject'] = Header(f"🚀 [透明版] 美股深度選股清單 ({date_str})", 'utf-8')
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.quit()
        print(f"✅ Email 已寄出！")
    except Exception as e:
        print(f"❌ 發送失敗: {e}")

if __name__ == "__main__":
    send_email_report()
