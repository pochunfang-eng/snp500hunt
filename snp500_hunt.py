import yfinance as yf
import pandas as pd
import requests
import io
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from email.header import Header
# ===========================
# 🔧 使用者設定區 (請修改這裡)
# ===========================
EMAIL_SENDER = "pochun.fang@gmail.com"
EMAIL_PASSWORD = "gecl zapv kywl rpdr"
EMAIL_RECEIVER = "pochun.fang@gmail.com, sdx9030122716@gmail.com"

# ===========================
# 1. 抓取 S&P 500 清單
# ===========================
def get_sp500_tickers():
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers)
        tables = pd.read_html(io.StringIO(response.text))
        target_df = None
        for df in tables:
            if len(df) > 400 and ('Symbol' in df.columns or 'Ticker' in df.columns):
                target_df = df
                break
        if target_df is None: raise ValueError
        col = 'Symbol' if 'Symbol' in target_df.columns else 'Ticker'
        return [str(t).replace('.', '-') for t in target_df[col].tolist()]
    except:
        # 備用清單
        return ['NVDA', 'AAPL', 'MSFT', 'AMZN', 'TSLA', 'AMD', 'GOOGL', 'META', 'AVGO', 'JPM']

# ===========================
# 2. K線型態判斷
# ===========================
def judge_price_action(open_p, high_p, low_p, close_p):
    body = abs(close_p - open_p)
    total_range = high_p - low_p
    upper_shadow = high_p - max(close_p, open_p)
    lower_shadow = min(close_p, open_p) - low_p
    
    if total_range == 0: return "盤整"
    if upper_shadow > (body * 1.5) and upper_shadow > lower_shadow: return "⚠️ 高檔遇壓"
    if close_p > open_p and body > (total_range * 0.6) and upper_shadow < (total_range * 0.2): return "🔥 強力進攻"
    if lower_shadow > (body * 2) and lower_shadow > upper_shadow: return "⚓️ 底部支撐"
    return "⬆️ 普通上漲" if close_p > open_p else "⬇️ 普通下跌"

# ===========================
# 3. 新增：基本面體檢函數
# ===========================
def check_fundamentals(ticker):
    """
    抓取單一股票的基本面數據
    回傳: (營收成長率, 獲利成長率, 本益比, 體質標籤)
    """
    try:
        # 稍微暫停避免被 API 封鎖
        time.sleep(0.5)
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # 取得數據 (若無數據則回傳 0)
        rev_growth = info.get('revenueGrowth', 0)
        eps_growth = info.get('earningsGrowth', 0)
        pe_ratio = info.get('forwardPE', info.get('trailingPE', 0))
        
        # 給予體質標籤
        tags = []
        # 營收或獲利成長 > 15% 視為高成長
        if rev_growth > 0.15 and eps_growth > 0.15:
            tags.append("💎 績優雙高")
        elif rev_growth > 0.15:
            tags.append("📈 營收爆發")
        elif eps_growth > 0.15:
            tags.append("💰 獲利提升")
        
        # 負成長警示
        if rev_growth < 0 or eps_growth < 0:
            tags.append("⚠️ 衰退疑慮")
            
        tag_str = " ".join(tags) if tags else "😐 平平"
        
        return rev_growth, eps_growth, pe_ratio, tag_str
        
    except:
        return 0, 0, 0, "❓ 無數據"

# ===========================
# 4. 核心分析 (技術 + 基本面整合)
# ===========================
def analyze_market_signals():
    tickers = get_sp500_tickers()
    print(f"⏳ [1/2] 正在掃描 S&P 500 技術面訊號 (需時約 60 秒)...")
    
    try:
        data = yf.download(tickers, period="2mo", auto_adjust=False, group_by='ticker', progress=False)
    except:
        return pd.DataFrame(), {}

    all_dates = data.index
    if len(all_dates) < 7: return pd.DataFrame(), {}
    target_dates = all_dates[-7:]
    
    stock_summary = {}
    stock_history = {}

    # --- Step 1: 技術面掃描 (找出候選股) ---
    for target_date in target_dates:
        try:
            idx = data.index.get_loc(target_date)
        except: continue
        if idx < 22: continue
        
        date_str = target_date.strftime('%Y-%m-%d')

        for stock in tickers:
            try:
                if stock not in data.columns.get_level_values(0): continue
                df = data[stock]
                
                today_C = df['Close'].iloc[idx]
                today_O = df['Open'].iloc[idx]
                today_H = df['High'].iloc[idx]
                today_L = df['Low'].iloc[idx]
                today_V = df['Volume'].iloc[idx]
                prev_C = df['Close'].iloc[idx-1]
                
                if pd.isna(today_C) or today_V == 0: continue

                pct_change = ((today_C - prev_C) / prev_C) * 100
                avg_vol = df['Volume'].iloc[idx-21:idx].mean()
                rvol = today_V / avg_vol if avg_vol > 0 else 0
                
                if pct_change > 1.0 and rvol > 1.5:
                    action = judge_price_action(today_O, today_H, today_L, today_C)
                    flow_score = pct_change * rvol
                    
                    if "普通" in action and flow_score < 3: continue

                    # 紀錄總結
                    if stock not in stock_summary:
                        stock_summary[stock] = {
                            '次數': 0, '總強度': 0, '最新型態': action,
                            '最新RVol': rvol, '最後出現日': target_date
                        }
                    stock_summary[stock]['次數'] += 1
                    stock_summary[stock]['總強度'] += flow_score
                    if target_date >= stock_summary[stock]['最後出現日']:
                        stock_summary[stock]['最新型態'] = action
                        stock_summary[stock]['最新RVol'] = rvol
                        stock_summary[stock]['最後出現日'] = target_date

                    # 紀錄歷史
                    if stock not in stock_history: stock_history[stock] = []
                    stock_history[stock].append({
                        '日期': date_str, '收盤': round(today_C, 2),
                        '漲幅': round(pct_change, 2), 'RVol': round(rvol, 2),
                        '型態': action
                    })
            except: continue

    # --- Step 2: 篩選候選名單並進行基本面檢查 ---
    final_results = []
    last_valid_date = target_dates[-1]

    # 先初步過濾出值得看的股票 (避免浪費時間查爛股)
    candidate_stocks = []
    for stock, info in stock_summary.items():
        days_diff = (last_valid_date - info['最後出現日']).days
        if days_diff > 3: continue # 過期不看
        candidate_stocks.append((stock, info))
    
    print(f"⏳ [2/2] 正在對 {len(candidate_stocks)} 檔候選股進行基本面體檢...")

    for stock, info in candidate_stocks:
        # 判斷技術信號
        signal = "👀 觀望"
        reason = ""
        style_color = "black"
        
        days_diff = (last_valid_date - info['最後出現日']).days

        if info['次數'] >= 2 and days_diff <= 1:
            if "高檔遇壓" not in info['最新型態']:
                signal = "★ 強力買入"
                reason = "連續資金流入"
                style_color = "#d35400"
        elif info['次數'] == 1 and days_diff == 0:
            if info['最新RVol'] > 2.0 and "強力" in info['最新型態']:
                signal = "⚡️ 爆發起漲"
                reason = "首日爆天量"
                style_color = "red"
            elif "底部" in info['最新型態']:
                signal = "⚓️ 抄底機會"
                reason = "低檔爆量支撐"
                style_color = "green"
        
        if "高檔遇壓" in info['最新型態']:
            signal = "⚠️ 危險勿追"
            reason = "爆量收避雷針"
            style_color = "gray"

        if signal != "👀 觀望":
            # === 關鍵新增：呼叫基本面檢查 ===
            rev_g, eps_g, pe, funda_tag = check_fundamentals(stock)
            
            # 如果基本面很好，加分
            if "績優" in funda_tag or "爆發" in funda_tag:
                info['總強度'] += 5  # 加分
                style_color = "#8e44ad" # 紫色代表優質飆股
            
            # 如果基本面衰退，在原因後面加註
            if "衰退" in funda_tag:
                reason += " (⚠️基本面衰退)"

            final_results.append({
                "股票": stock,
                "信號": signal,
                "原因": reason,
                "7日次數": info['次數'],
                "RVol": round(info['最新RVol'], 1),
                "型態": info['最新型態'],
                "總分": round(info['總強度'], 1),
                "Color": style_color,
                # 新增基本面欄位
                "基本面標籤": funda_tag,
                "營收成長": f"{rev_g:.1%}" if rev_g else "-",
                "獲利成長": f"{eps_g:.1%}" if eps_g else "-"
            })

    if not final_results:
        return pd.DataFrame(), {}
        
    df = pd.DataFrame(final_results)
    df_sorted = df.sort_values(by="總分", ascending=False).head(15)
    
    filtered_history = {k: v for k, v in stock_history.items() if k in df_sorted['股票'].values}
    
    return df_sorted, filtered_history
# ===========================
# 5. 發送 Email (修正編碼問題版)
# ===========================
def send_email_report():
    df, history_data = analyze_market_signals()
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # ... (中間產生 summary_table 和 history_html 的 HTML 產生邏輯不變) ...
    # 為了節省版面，這裡假設你已經產生好 html_body 了
    # 如果你需要完整函式，請往下看完整代碼
    
    if not df.empty:
        # (這裡填入原本產生 HTML 表格的程式碼...)
        # ...
        summary_rows = ""
        for _, row in df.iterrows():
            funda_style = "color: red;" if "衰退" in row['基本面標籤'] else "color: green;" if "績優" in row['基本面標籤'] else "color: gray;"
            summary_rows += f"""
            <tr style="border-bottom: 1px solid #ddd;">
                <td style="padding: 8px; text-align: center; font-size: 15px;"><b>{row['股票']}</b></td>
                <td style="padding: 8px; text-align: center; color: {row['Color']}; font-weight: bold;">{row['信號']}</td>
                <td style="padding: 8px; text-align: center; font-size: 12px;">{row['原因']}</td>
                <td style="padding: 8px; text-align: center; font-size: 12px; {funda_style}"><b>{row['基本面標籤']}</b><br>營:{row['營收成長']}<br>利:{row['獲利成長']}</td>
                <td style="padding: 8px; text-align: center;">{row['7日次數']}</td>
                <td style="padding: 8px; text-align: center;">{row['RVol']}</td>
                <td style="padding: 8px; text-align: center;">{row['型態']}</td>
            </tr>
            """
        
        summary_table = f"""<table style="border-collapse: collapse; width: 100%; background-color: white;"><thead><tr style="background-color: #f2f2f2;"><th style="padding: 8px; border: 1px solid #ddd;">股票</th><th style="padding: 8px; border: 1px solid #ddd;">信號</th><th style="padding: 8px; border: 1px solid #ddd;">原因</th><th style="padding: 8px; border: 1px solid #ddd;">基本面體質</th><th style="padding: 8px; border: 1px solid #ddd;">次數</th><th style="padding: 8px; border: 1px solid #ddd;">RVol</th><th style="padding: 8px; border: 1px solid #ddd;">型態</th></tr></thead><tbody>{summary_rows}</tbody></table>"""
        
        history_html = ""
        for stock_name in df['股票']:
            records = history_data.get(stock_name, [])
            if not records: continue
            sub_rows = ""
            for r in records:
                sub_rows += f"""<tr style="border-bottom: 1px solid #eee; font-size: 13px;"><td style="padding: 5px;">{r['日期']}</td><td style="padding: 5px;">{r['收盤']}</td><td style="padding: 5px; color: red;">+{r['漲幅']}%</td><td style="padding: 5px;"><b>{r['RVol']}</b></td><td style="padding: 5px;">{r['型態']}</td></tr>"""
            history_html += f"""<div style="margin-bottom: 15px; background-color: #fafafa; padding: 10px; border-left: 4px solid #8e44ad;"><h4 style="margin: 0 0 5px 0; color: #2c3e50;">📈 {stock_name} 交易紀錄</h4><table style="width: 100%; border-collapse: collapse;"><tbody>{sub_rows}</tbody></table></div>"""

        status_msg = "🔥 今日 S&P 500 【技術+基本面】雙刀流掃描結果："
    else:
        summary_table = "<p>今日無顯著信號。</p>"
        history_html = ""
        status_msg = "⚠️ 今日無信號"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #2c3e50; border-bottom: 2px solid #2c3e50;">📊 美股全方位狙擊日報 ({date_str})</h2>
        <p>{status_msg}</p>
        <h3 style="background-color: #2980b9; color: white; padding: 5px;">🏆 最佳買入機會 (Top 15)</h3>
        {summary_table}
        <br>
        <h3 style="background-color: #27ae60; color: white; padding: 5px;">📂 歷史爆量證據</h3>
        {history_html}
        <div style="font-size: 12px; color: gray; margin-top: 20px;">*基本面數據：營=營收成長率(YoY), 利=獲利成長率(YoY)。<br>*此報表僅供參考，不構成投資建議。</div>
    </body>
    </html>
    """

    # --- ⚠️ 這裡是最關鍵的修正 ---
    msg = MIMEMultipart()
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER
    
    # 修正 1: 使用 Header 物件並指定 utf-8 來處理標題中的中文和 Emoji
    msg['Subject'] = Header(f"🚀 [終極版] 美股 S&P 500 雙刀流選股報告 ({date_str})", 'utf-8')
    
    # 修正 2: 在 MIMEText 加入第三個參數 'utf-8'
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"✅ 報告已寄出！")
    except Exception as e:
        print(f"❌ 發送失敗: {e}")

if __name__ == "__main__":
    send_email_report()
