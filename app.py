import time
import datetime
import streamlit as st
import pandas as pd
import numpy as np
import requests
import yfinance as yf

# 尝试加载 curl_cffi 进行 Chrome TLS 指纹伪装
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# ==============================================================================
# 1. 核心缓存引擎 (1小时内相同参数不重复请求雅虎 API，彻底防 Rate Limit)
# ==============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ticker_options_safe(symbol, budget, min_vol, min_open_int, min_b_price, min_ann_ret, min_d, max_d, avoid_earn):
    """单只股票的安全抓取与解析函数，带缓存与详细排查日志"""
    records = []
    diag = {
        "代码": symbol,
        "HTTP状态": "未请求",
        "抓取现价": "N/A",
        "可用到期日": 0,
        "符合条件合约数": 0,
        "排查结论": "未完成扫描"
    }
    
    try:
        ticker = yf.Ticker(symbol)
        
        # 1. 抓取标的现价
        current_price = None
        try:
            fast_info = ticker.fast_info
            current_price = getattr(fast_info, 'last_price', None)
        except Exception:
            pass
            
        if current_price is None or np.isnan(current_price) or current_price <= 0:
            hist = ticker.history(period="1d")
            if not hist.empty:
                current_price = float(hist['Close'].iloc[-1])

        if not current_price or np.isnan(current_price):
            diag["HTTP状态"] = "报错/空数据"
            diag["排查结论"] = "❌ 无法获取最新股价 (可能触发 IP 频控)"
            return records, diag

        diag["HTTP状态"] = "200 (正常)"
        diag["抓取现价"] = f"${current_price:.2f}"
        
        # 2. 价格区间过滤 ($2.0 ~ 预算最高行权价 * 1.35)
        max_allowed_price = (budget / 100.0) * 1.35
        if current_price < 2.0 or current_price > max_allowed_price:
            diag["排查结论"] = f"⚠️ 现价 (${current_price:.2f}) 超出预算允许匹配区间 ($2.0 ~ ${max_allowed_price:.1f})"
            return records, diag

        # 3. 提取期权到期日
        dates = ticker.options
        if not dates:
            diag["排查结论"] = "❌ 未找到可用期权到期日"
            return records, diag
            
        diag["可用到期日"] = len(dates)

        # 4. 提取财报日 (避险过滤)
        next_earnings_date = None
        if avoid_earn:
            try:
                calendar = ticker.calendar
                if isinstance(calendar, dict) and 'Earnings Date' in calendar:
                    e_dates = calendar['Earnings Date']
                    if e_dates and len(e_dates) > 0:
                        next_earnings_date = pd.to_datetime(e_dates[0]).date()
                elif isinstance(calendar, pd.DataFrame) and not calendar.empty:
                    if 'Earnings Date' in calendar.index:
                        e_dates = calendar.loc['Earnings Date'].values
                        if len(e_dates) > 0:
                            next_earnings_date = pd.to_datetime(e_dates[0]).date()
            except Exception:
                pass

        today = datetime.date.today()
        max_strike_price = budget / 100.0

        # 5. 遍历到期日拉取 Put 期权链
        for d_str in dates:
            exp_date = datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days

            # DTE 时间窗过滤
            if not (min_d <= dte <= max_d):
                continue

            # 财报避险过滤
            if avoid_earn and next_earnings_date:
                if today < next_earnings_date <= exp_date:
                    continue

            try:
                opt_chain = ticker.option_chain(d_str)
                puts = opt_chain.puts
                if puts.empty:
                    continue

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
                    iv = float(row['impliedVolatility']) if 'impliedVolatility' in row and not pd.isna(row['impliedVolatility']) else 0.0

                    # 计算年化与保证金
                    annual_return = (bid / strike) * (365.0 / dte) * 100.0
                    if annual_return < min_ann_ret:
                        continue

                    margin_required = strike * 100.0
                    premium_collected = bid * 100.0
                    safety_buffer = ((current_price - strike) / current_price) * 100.0

                    records.append({
                        "代码": symbol,
                        "标的现价 ($)": round(current_price, 2),
                        "到期日": d_str,
                        "DTE (天)": dte,
                        "行权价 ($)": strike,
                        "安全边际": f"{safety_buffer:.1f}%",
                        "买一价 (Bid $)": bid,
                        "卖一价 (Ask $)": ask,
                        "单张权利金 ($)": round(premium_collected, 2),
                        "保证金/单笔 ($)": round(margin_required, 2),
                        "年化收益率": round(annual_return, 2),
                        "隐含波动率 (IV)": f"{iv * 100:.1f}%",
                        "成交量 (张)": volume,
                        "持仓量 (张)": open_interest
                    })

            except Exception as opt_err:
                err_str = str(opt_err)
                if "Rate limited" in err_str or "429" in err_str or "401" in err_str:
                    diag["排查结论"] = "⚠️ 触发雅虎频控 (Rate Limited)，部分日期跳过"
                    break
                continue

        diag["符合条件合约数"] = len(records)
        if len(records) > 0:
            diag["排查结论"] = "✅ 扫描成功"
        else:
            diag["排查结论"] = "⚠️ 无符合当前 Bid/成交量/年化门槛的合约"

    except Exception as e:
        err_msg = str(e)
        if "Rate limited" in err_msg or "429" in err_msg:
            diag["排查结论"] = "❌ 触发雅虎 API 频率限制 (Rate Limited)"
        else:
            diag["排查结论"] = f"❌ 运行异常: {err_msg}"

    return records, diag

# ==============================================================================
# 2. 页面全局配置与 UI 样式设定
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
# 3. 股票池内置预设 (完整维持初心：小盘低股价练手标的池)
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
# 5. 合并股票池逻辑
# ==============================================================================
def get_combined_watchlist():
    base_list = PRESET_WATCHLISTS[selected_pool_name]
    if custom_tickers_input.strip():
        add_list = [x.strip().upper() for x in custom_tickers_input.replace(',', ' ').split() if x.strip()]
        combined = list(dict.fromkeys(base_list + add_list))
    else:
        combined = base_list
    return combined

# ==============================================================================
# 6. 主逻辑渲染与诊断结果面板
# ==============================================================================
if start_btn:
    watchlist = get_combined_watchlist()
    total_tickers = len(watchlist)
    
    st.write(f"🔍 正在对 **{total_tickers}** 只标的进行缓冲防封扫描与诊断分析...")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    all_results = []
    diag_logs = []
    
    for idx, sym in enumerate(watchlist):
        status_text.text(f"正在扫描 [{idx+1}/{total_tickers}]: {sym} ...")
        
        res, diag = fetch_ticker_options_safe(
            symbol=sym,
            budget=budget,
            min_vol=min_volume,
            min_open_int=min_oi,
            min_b_price=min_bid,
            min_ann_ret=min_annual_return,
            min_d=min_dte,
            max_d=max_dte,
            avoid_earn=earnings_avoid
        )
        all_results.extend(res)
        diag_logs.append(diag)
        
        progress_bar.progress((idx + 1) / total_tickers)
        
        # 核心防封锁缓冲：每次请求间隔 0.6 秒，平摊 API 调用频次
        time.sleep(0.6)
        
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
        st.download_button(
            label="📥 导出筛选结果为 CSV 文件",
            data=csv_data,
            file_name=f"Sell_Put_Scan_{datetime.date.today()}.csv",
            mime="text/csv",
            type="secondary"
        )
    else:
        st.warning("🤖 未找到符合要求的标的！请展开下方【🛠️ 接口抓取与风控排查明细】查看具体诊断原因。")

    # 诊断面板：精准排查频控或过滤原因
    with st.expander("🛠️ 接口抓取与风控排查明细 (点击展开诊断面板)", expanded=True):
        df_diag = pd.DataFrame(diag_logs)
        st.dataframe(df_diag, use_container_width=True)

else:
    st.info("👈 请在侧边栏配置筛选风控参数，然后点击 **【🚀 启动 4.0 全能量化扫描】** 开始运行。")
