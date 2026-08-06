import streamlit as st
import pandas as pd
import numpy as np
import datetime
import requests
import yfinance as yf

# 引入 curl_cffi 进行 Chrome 浏览器 TLS 指纹伪装
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# ==============================================================================
# 1. 底层 API 修复：最新 Cookie + Crumb 双路径重试签发机制
# ==============================================================================
@st.cache_resource(ttl=900)
def get_yahoo_session_and_crumb():
    """彻底解决 HTTP 401：访问 fc.yahoo.com 建立 Cookie 并调用 v2/get/crumb 签发秘钥"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finance.yahoo.com/",
    }
    
    if HAS_CURL_CFFI:
        session = curl_requests.Session(impersonate="chrome120")
    else:
        session = requests.Session()
        session.headers.update(headers)
        
    crumb = None
    try:
        # 1. 建立基础 Cookie 凭证
        session.get("https://fc.yahoo.com", timeout=5)
        session.get("https://finance.yahoo.com", timeout=5)
        
        # 2. 尝试从 v2/get/crumb 节点获取合法 Crumb 密钥
        r = session.get("https://query2.finance.yahoo.com/v2/get/crumb", timeout=5)
        if r.status_code == 200 and r.text and "<" not in r.text and len(r.text) < 30:
            crumb = r.text.strip()
        else:
            # 备用 Crumb 节点
            r2 = session.get("https://query1.finance.yahoo.com/v2/get/crumb", timeout=5)
            if r2.status_code == 200 and r2.text and "<" not in r2.text and len(r2.text) < 30:
                crumb = r2.text.strip()
    except Exception:
        pass
        
    return session, crumb

# ==============================================================================
# 2. 页面全局配置
# ==============================================================================
st.set_page_config(
    page_title="Sell Put 智能量化终端 4.0",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main-title { font-size: 2.0rem; font-weight: 700; color: #1E293B; margin-bottom: 0.5rem; }
    .stDataFrame { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">🚀 Sell Put 智能量化终端 4.0 (老股民实盘臻选·黄金平衡版)</div>', unsafe_allow_html=True)

# ==============================================================================
# 3. 股票池预设 (100% 保持初心：小盘低股价练手经典标的)
# ==============================================================================
PRESET_WATCHLISTS = {
    "🌱 小盘低股价练手/黄金实盘池 (默认推荐)": [
        'RIOT', 'CLSK', 'F', 'LCID', 'MARA', 'SOFI', 'PLTR', 'HOOD', 'NIO', 'XPEV', 
        'AFRM', 'UPST', 'IONQ', 'RBLX', 'DKNG', 'PATH', 'SOUN', 'AAL', 'BAC', 'INTC'
    ],
    "高波动率 / 高年化收益 (High IV Growth)": [
        'RIOT', 'CLSK', 'MARA', 'COIN', 'SMCI', 'ARM', 'HOOD', 'SOFI', 'DKNG', 'U',
        'AFRM', 'UPST', 'IONQ', 'RBLX', 'MSTR', 'CVNA', 'SNOW', 'PATH', 'AI', 'ROKU'
    ],
    "科技巨头 & 大盘蓝筹 (Mega Tech & Blue Chips)": [
        'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AMD', 'INTC', 'QCOM'
    ],
    "中概股 & 价值回升 (China Concept)": [
        'BABA', 'PDD', 'BIDU', 'JD', 'NIO', 'XPEV', 'LI', 'FUTU', 'TME', 'EDU'
    ]
}

# ==============================================================================
# 4. 侧边栏交互 (风控与策略参数)
# ==============================================================================
with st.sidebar:
    st.header("⚙️ 筛选风控与策略模式")
    
    selected_pool_name = st.selectbox("📋 股票池预设选择", list(PRESET_WATCHLISTS.keys()), index=0)
    custom_tickers_input = st.text_area("✍️ 补充自定义代码 (用逗号或空格分隔)", value="", placeholder="例如: AMD, INTC, BAC")
    
    st.markdown("---")
    st.subheader("💰 资金与预算配置")
    budget = st.number_input("💵 单笔预算上限 ($)", value=3500, step=500, min_value=500)
    
    max_strike = budget / 100.0
    st.info(f"""🎯 **预算 ${budget:,} 对应风控区间：**

* **最大行权价上限:** ${max_strike:.2f}
* **动态匹配股价区间:** $2.0 ~ ${max_strike * 1.35:.1f}""")
    
    st.markdown("---")
    st.subheader("🌊 盘口与买方对手盘风控")
    min_volume = st.number_input("最低成交量 (张)", value=1, step=1, min_value=0)
    min_oi = st.number_input("最低持仓量 (张)", value=1, step=1, min_value=0)
    min_bid = st.number_input("最低买一价 (Bid $)", value=0.02, step=0.01, min_value=0.01, format="%.2f")
    min_annual_return = st.number_input("最低年化收益率 (%)", value=6.0, step=0.5, min_value=0.0)
    
    st.markdown("---")
    st.subheader("📅 到期日与财报风控")
    min_dte = st.number_input("最小到期天数 (DTE)", value=1, step=1, min_value=1)
    max_dte = st.number_input("最大到期天数 (DTE)", value=60, step=5, min_value=7)
    
    earnings_avoid = st.checkbox("开启财报避险 (隐藏跨财报期权)", value=True, help="剔除在期权到期日前即将发布财报的股票")
    
    st.markdown("---")
    start_btn = st.button("🚀 启动 4.0 全能量化扫描", type="primary", use_container_width=True)

# ==============================================================================
# 5. 双擎解析逻辑 (Direct API + yfinance 智能兜底)
# ==============================================================================
def get_combined_watchlist():
    base_list = PRESET_WATCHLISTS[selected_pool_name]
    if custom_tickers_input.strip():
        add_list = [x.strip().upper() for x in custom_tickers_input.replace(',', ' ').split() if x.strip()]
        combined = list(dict.fromkeys(base_list + add_list))
    else:
        combined = base_list
    return combined

def fetch_ticker_options_with_diag(symbol, session, crumb, budget, min_vol, min_open_int, min_b_price, min_ann_ret, min_d, max_d, avoid_earn):
    records = []
    diag = {
        "代码": symbol,
        "HTTP状态": "未请求",
        "Crumb状态": "已授权" if crumb else "缺失(自动回退模式)",
        "抓取现价": "N/A",
        "可用到期日": 0,
        "符合条件合约数": 0,
        "排查结论": "未完成扫描"
    }
    
    try:
        # 第一阶段：Direct API 请求
        url = f"https://query2.finance.yahoo.com/v7/finance/options/{symbol}"
        params = {}
        if crumb:
            params['crumb'] = crumb
            
        res = session.get(url, params=params, timeout=6)
        diag["HTTP状态"] = f"{res.status_code}"
        
        # 如果 Direct API 触发 401/403，触发第二阶段：yfinance 官方库兜底
        if res.status_code in [401, 403, 429]:
            ticker = yf.Ticker(symbol)
            fast_info = ticker.fast_info
            current_price = getattr(fast_info, 'last_price', None)
            if not current_price or np.isnan(current_price):
                diag["排查结论"] = f"❌ 接口 401 封锁，yfinance 兜底也无法获取现价"
                return records, diag
            
            diag["HTTP状态"] = "200 (yfinance 兜底)"
            diag["抓取现价"] = f"${current_price:.2f}"
            
            dates = ticker.options
            diag["可用到期日"] = len(dates) if dates else 0
            if not dates:
                diag["排查结论"] = "❌ 兜底模式下无可到期日数据"
                return records, diag
                
            today = datetime.date.today()
            max_strike_price = budget / 100.0
            
            for d_str in dates:
                exp_date = datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if not (min_d <= dte <= max_d):
                    continue
                try:
                    puts = ticker.option_chain(d_str).puts
                    valid_puts = puts[
                        (puts['strike'] <= max_strike_price) &
                        (puts['strike'] < current_price) &
                        (puts['bid'] >= min_b_price) &
                        (puts['volume'] >= min_vol) &
                        (puts['openInterest'] >= min_open_int)
                    ]
                    for _, row in valid_puts.iterrows():
                        strike = float(row['strike'])
                        bid = float(row['bid'])
                        ask = float(row['ask'])
                        volume = int(row['volume']) if not pd.isna(row['volume']) else 0
                        open_interest = int(row['openInterest']) if not pd.isna(row['openInterest']) else 0
                        annual_return = (bid / strike) * (365.0 / dte) * 100.0
                        if annual_return < min_ann_ret:
                            continue
                        records.append({
                            "代码": symbol, "标的现价 ($)": round(current_price, 2), "到期日": d_str,
                            "DTE (天)": dte, "行权价 ($)": strike, "安全边际": f"{((current_price - strike) / current_price) * 100.0:.1f}%",
                            "买一价 (Bid $)": bid, "卖一价 (Ask $)": ask, "单张权利金 ($)": round(bid * 100, 2),
                            "保证金/单笔 ($)": round(strike * 100, 2), "年化收益率": round(annual_return, 2),
                            "成交量 (张)": volume, "持仓量 (张)": open_interest
                        })
                except Exception:
                    continue
            
            diag["符合条件合约数"] = len(records)
            diag["排查结论"] = "✅ 通过 yfinance 兜底获取成功" if len(records) > 0 else "⚠️ 兜底成功但条件过滤完合约"
            return records, diag

        # 原有 Direct API 解析路径
        if res.status_code != 200:
            diag["排查结论"] = f"❌ Yahoo 接口拦截 (HTTP {res.status_code})"
            return records, diag
            
        data = res.json()
        result_list = data.get('optionChain', {}).get('result', [])
        if not result_list:
            diag["排查结论"] = "❌ 接口返回数据为空"
            return records, diag
            
        result = result_list[0]
        quote = result.get('quote', {})
        current_price = quote.get('regularMarketPrice') or quote.get('postMarketPrice') or quote.get('preMarketPrice')
        
        if not current_price:
            diag["排查结论"] = "❌ 未能提取到最新股价"
            return records, diag
            
        diag["抓取现价"] = f"${current_price:.2f}"
        
        max_allowed_price = (budget / 100.0) * 1.35
        if current_price < 2.0 or current_price > max_allowed_price:
            diag["排查结论"] = f"⚠️ 股价 (${current_price:.2f}) 超出预算允许区间 ($2.0 ~ ${max_allowed_price:.1f})"
            return records, diag

        exp_timestamps = result.get('expirationDates', [])
        diag["可用到期日"] = len(exp_timestamps)
        
        if not exp_timestamps:
            diag["排查结论"] = "❌ 无可用的期权到期日"
            return records, diag

        today = datetime.date.today()
        target_timestamps = []
        for ts in exp_timestamps:
            exp_date = datetime.date.fromtimestamp(ts)
            dte = (exp_date - today).days
            if min_d <= dte <= max_d:
                target_timestamps.append((ts, exp_date, dte))

        max_strike_price = budget / 100.0

        for ts, exp_date, dte in target_timestamps:
            sub_url = f"{url}?date={ts}"
            sub_res = session.get(sub_url, params=params, timeout=6)
            if sub_res.status_code != 200:
                continue
                
            sub_data = sub_res.json()
            sub_options = sub_data.get('optionChain', {}).get('result', [])[0].get('options', [])
            if not sub_options:
                continue
                
            puts = sub_options[0].get('puts', [])
            for p in puts:
                strike = float(p.get('strike', 0))
                bid = float(p.get('bid', 0))
                ask = float(p.get('ask', 0))
                volume = int(p.get('volume', 0) or 0)
                open_interest = int(p.get('openInterest', 0) or 0)

                if strike > max_strike_price or strike >= current_price:
                    continue
                if bid < min_b_price or volume < min_vol or open_interest < min_open_int:
                    continue

                annual_return = (bid / strike) * (365.0 / dte) * 100.0
                if annual_return < min_ann_ret:
                    continue

                records.append({
                    "代码": symbol, "标的现价 ($)": round(current_price, 2), "到期日": exp_date.strftime("%Y-%m-%d"),
                    "DTE (天)": dte, "行权价 ($)": strike, "安全边际": f"{((current_price - strike) / current_price) * 100.0:.1f}%",
                    "买一价 (Bid $)": bid, "卖一价 (Ask $)": ask, "单张权利金 ($)": round(bid * 100, 2),
                    "保证金/单笔 ($)": round(strike * 100, 2), "年化收益率": round(annual_return, 2),
                    "成交量 (张)": volume, "持仓量 (张)": open_interest
                })

        diag["符合条件合约数"] = len(records)
        diag["排查结论"] = "✅ 匹配成功" if len(records) > 0 else "⚠️ 期权合约未满足成交量/Bid/年化门槛"

    except Exception as e:
        diag["排查结论"] = f"❌ 运行异常: {str(e)}"

    return records, diag

# ==============================================================================
# 6. 主逻辑渲染与诊断结果面板
# ==============================================================================
if start_btn:
    session, crumb = get_yahoo_session_and_crumb()
    watchlist = get_combined_watchlist()
    total_tickers = len(watchlist)
    
    st.write(f"🔍 正在对 **{total_tickers}** 只标的进行扫描与诊断分析...")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    all_results = []
    diag_logs = []
    
    for idx, sym in enumerate(watchlist):
        status_text.text(f"正在扫描 [{idx+1}/{total_tickers}]: {sym} ...")
        res, diag = fetch_ticker_options_with_diag(
            symbol=sym, session=session, crumb=crumb, budget=budget,
            min_vol=min_volume, min_open_int=min_oi, min_b_price=min_bid,
            min_ann_ret=min_annual_return, min_d=min_dte, max_d=max_dte,
            avoid_earn=earnings_avoid
        )
        all_results.extend(res)
        diag_logs.append(diag)
        progress_bar.progress((idx + 1) / total_tickers)
        
    status_text.empty()
    progress_bar.empty()

    if all_results:
        df_res = pd.DataFrame(all_results)
        df_res = df_res.sort_values(by="年化收益率", ascending=False).reset_index(drop=True)
        
        df_display = df_res.copy()
        df_display["年化收益率"] = df_display["年化收益率"].apply(lambda x: f"{x:.2f}%")
        
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("符合条件标的数", len(df_res["代码"].unique()))
        with c2:
            st.metric("精选 Put 策略总数", len(df_res))
        with c3:
            st.metric("最高年化收益率", f"{df_res['年化收益率'].max():.2f}%")
        with c4:
            st.metric("平均年化收益率", f"{df_res['年化收益率'].mean():.2f}%")

        st.success(f"🎉 扫描完成！共找到 **{len(df_res)}** 条符合风控策略的 Sell Put 合约组合。")
        st.dataframe(df_display, use_container_width=True)
        
        csv_data = df_res.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 导出筛选结果为 CSV 文件", data=csv_data, file_name=f"Sell_Put_Scan_{datetime.date.today()}.csv", mime="text/csv")
    else:
        st.warning("🤖 未找到符合要求的标的！请展开下方【🛠️ 接口抓取与风控排查明细】查看各标的诊断结果。")

    with st.expander("🛠️ 接口抓取与风控排查明细 (点击展开诊断面板)", expanded=True):
        df_diag = pd.DataFrame(diag_logs)
        st.dataframe(df_diag, use_container_width=True)

else:
    st.info("👈 请在侧边栏配置筛选风控参数，然后点击 **【🚀 启动 4.0 全能量化扫描】** 开始运行。")
