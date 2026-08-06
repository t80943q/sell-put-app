import time
import datetime
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.express as px

# ==============================================================================
# 1. 页面配置与全局样式
# ==============================================================================
st.set_page_config(page_title="Sell Put 4.0 机构级智能终端", page_icon="🏦", layout="wide")

st.markdown("""
<style>
    .main-title { font-size: 2.2rem; font-weight: 700; margin-bottom: 0.5rem; }
    .sub-title { font-size: 1.5rem; font-weight: 600; margin-top: 2rem; margin-bottom: 1rem; color: #EAB308;}
    .metric-card { background-color: #1E293B; padding: 15px; border-radius: 8px; text-align: center; border: 1px solid #334155; }
    .metric-value { font-size: 1.8rem; font-weight: bold; color: #10B981; }
    .metric-label { font-size: 1rem; color: #94A3B8; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">🏦 Sell Put 4.0 机构级智能终端 (现金流与实盘融合版)</div>', unsafe_allow_html=True)

# ==============================================================================
# 2. 核心数据引擎
# ==============================================================================
@st.cache_data(ttl=600, show_spinner=False)
def fetch_ticker_options_safe(symbol, budget, min_vol, min_open_int, min_b_price, min_ann_ret, min_d, max_d, avoid_earn):
    records = []
    diag = {"代码": symbol, "HTTP状态": "未请求", "抓取现价": "N/A", "可用到期日": 0, "符合条件合约数": 0, "排查结论": "未完成扫描"}
    
    try:
        ticker = yf.Ticker(symbol)
        
        current_price = None
        try:
            current_price = getattr(ticker.fast_info, 'last_price', None)
        except: pass
        if not current_price or np.isnan(current_price) or current_price <= 0:
            try:
                hist = ticker.history(period="1d")
                if not hist.empty: current_price = float(hist['Close'].iloc[-1])
            except: pass

        if not current_price or np.isnan(current_price):
            diag.update({"HTTP状态": "报错/空数据", "排查结论": "❌ 无法获取最新股价"})
            return records, diag

        diag.update({"HTTP状态": "200 (正常)", "抓取现价": f"${current_price:.2f}"})
        
        max_allowed_price = (budget / 100.0) * 1.35
        if current_price < 2.0 or current_price > max_allowed_price:
            diag["排查结论"] = f"⚠️ 现价 (${current_price:.2f}) 超出单笔预算上限允许的行权价区间"
            return records, diag

        try: dates = ticker.options
        except Exception as e:
            diag["排查结论"] = f"❌ 期权链拉取失败: {str(e)}"
            return records, diag

        if not dates:
            diag["排查结论"] = "❌ 无可用期权到期日"
            return records, diag
        diag["可用到期日"] = len(dates)

        next_earnings_date = None
        if avoid_earn:
            try:
                calendar = ticker.calendar
                if isinstance(calendar, dict) and 'Earnings Date' in calendar:
                    e_dates = calendar['Earnings Date']
                    if e_dates: next_earnings_date = pd.to_datetime(e_dates[0]).date()
                elif isinstance(calendar, pd.DataFrame) and not calendar.empty:
                    if 'Earnings Date' in calendar.index:
                        e_dates = calendar.loc['Earnings Date'].values
                        if len(e_dates) > 0: next_earnings_date = pd.to_datetime(e_dates[0]).date()
            except: pass

        today = datetime.date.today()
        max_strike = budget / 100.0

        for d_str in dates:
            try: exp_date = datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
            except: continue

            dte = (exp_date - today).days
            if not (min_d <= dte <= max_d): continue

            is_cross_earnings = False
            if next_earnings_date and (today < next_earnings_date <= exp_date):
                is_cross_earnings = True
                if avoid_earn: continue

            try:
                opt_chain = ticker.option_chain(d_str)
                puts = opt_chain.puts
                if puts is None or puts.empty: continue

                puts['volume'] = puts['volume'].fillna(0)
                puts['openInterest'] = puts['openInterest'].fillna(0)
                puts['bid'] = puts['bid'].fillna(0.0)
                
                valid_puts = puts[(puts['strike'] <= max_strike) & (puts['strike'] < current_price) & 
                                  (puts['bid'] >= min_b_price) & (puts['volume'] >= min_vol) & 
                                  (puts['openInterest'] >= min_open_int)]

                for _, row in valid_puts.iterrows():
                    strike, bid = float(row['strike']), float(row['bid'])
                    ask = float(row['ask']) if not pd.isna(row.get('ask')) and row['ask'] > 0 else bid
                    iv = float(row['impliedVolatility']) if not pd.isna(row.get('impliedVolatility')) else 0.0
                    delta = float(row['delta']) if not pd.isna(row.get('delta')) else 0.0

                    if dte <= 0: continue
                    annual_return = (bid / strike) * (365.0 / dte) * 100.0
                    if annual_return < min_ann_ret: continue

                    margin = strike * 100.0
                    premium = bid * 100.0
                    safety_buf = ((current_price - strike) / current_price) * 100.0
                    score = round(annual_return + safety_buf, 2)

                    yymmdd = exp_date.strftime("%y%m%d")
                    moomoo_code = f"US.{symbol}{yymmdd}P{int(round(strike * 1000)):08d}"
                    unique_id = f"[{symbol}] 到期日:{d_str} | 行权价:${strike} | 权利金:${premium:.2f} (年化:{annual_return:.1f}%)"

                    records.append({
                        "Unique_ID": unique_id,
                        "代码": symbol,
                        "综合评分": score,
                        "标的现价": current_price,
                        "到期日": d_str,
                        "DTE(天)": dte,
                        "行权价($)": strike,
                        "推荐挂单价(Bid)": bid,
                        "需保证金($)": margin,
                        "单张权利金($)": premium,
                        "安全边际(%)": safety_buf,
                        "年化收益率(%)": annual_return,
                        "IV环境": f"{iv*100:.1f}%",
                        "Delta": round(delta, 3) if delta != 0 else "N/A",
                        "成交量": int(row['volume']),
                        "持仓量": int(row['openInterest']),
                        "财报预警": "⚠️ 跨财报" if is_cross_earnings else "✅ 安全",
                        "Moomoo 代码": moomoo_code,
                        "实盘挂单指令": f"Sell Put {symbol} {d_str} Strike ${strike} @ Bid ${bid}"
                    })
            except: continue

        diag["符合条件合约数"] = len(records)
        diag["排查结论"] = "✅ 扫描成功" if records else f"⚠️ 找到 {len(dates)} 个到期日，全被策略参数过滤 (建议检查财报或调低门槛)"
    except Exception as e:
        diag["排查结论"] = f"❌ 运行异常: {str(e)}"
    return records, diag

# ==============================================================================
# 3. 侧边栏风控与策略模式
# ==============================================================================
PRESET_WATCHLISTS = {
    "🌱 黄金实盘池 (默认推荐)": ['RIOT', 'CLSK', 'F', 'LCID', 'MARA', 'SOFI', 'PLTR', 'HOOD', 'NIO', 'XPEV', 'AFRM', 'UPST', 'IONQ', 'RBLX', 'DKNG', 'PATH', 'SOUN', 'AAL', 'BAC', 'INTC'],
    "📈 高波动率 (High IV Growth)": ['COIN', 'SMCI', 'ARM', 'U', 'MSTR', 'CVNA', 'SNOW', 'AI', 'ROKU'],
    "🏛️ 稳健指数 & 行业 ETF": ['TQQQ', 'SOXL', 'IWM', 'QQQ', 'SPY', 'ARKK', 'KWEB', 'XLE', 'XLF', 'SMH', 'TLT', 'GDX'],
    "💻 科技巨头 (Mega Tech)": ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AMD', 'QCOM']
}

with st.sidebar:
    st.header("⚙️ 筛选风控与策略模式")
    selected_pool = st.selectbox("📋 股票池选择", list(PRESET_WATCHLISTS.keys()), index=0)
    custom_tickers = st.text_area("✍️ 自定义代码 (空格/逗号分隔)", value="")
    
    st.markdown("---")
    budget = st.number_input("💵 单笔预算上限 ($)", value=100000, step=5000)
    min_volume = st.number_input("最低成交量 (张)", value=0, step=1)
    min_oi = st.number_input("最低持仓量 (张)", value=0, step=10)
    min_bid = st.number_input("最低买一价 (Bid $)", value=0.01, step=0.01) 
    min_annual_return = st.number_input("最低年化收益率 (%)", value=3.0, step=0.5) 
    
    st.markdown("---")
    min_dte = st.number_input("最小到期天数 (DTE)", value=1, min_value=1)
    max_dte = st.number_input("最大到期天数 (DTE)", value=180, min_value=1)
    avoid_earn = st.checkbox("开启财报避险 (隐藏跨财报期权)", value=False)
    
    # --- 智能预警系统：当长周期与财报避险冲突时报警 ---
    if avoid_earn and max_dte > 80:
        st.warning("⚠️ **策略逻辑冲突警告**：美股每 90 天发一次财报。你将最大天数设为了 180 天，若开启『财报避险』，所有远期合约必将跨越财报日并被系统强制删除！**建议在做长周期时取消勾选此项**。")
    
    st.markdown("---")
    if st.button("🧹 清理系统缓存"): 
        st.cache_data.clear()
        if 'scan_df' in st.session_state: del st.session_state['scan_df']
        if 'diag_logs' in st.session_state: del st.session_state['diag_logs']
        st.success("缓存与内存会话已清除！")
        
    start_btn = st.button("🚀 启动 4.0 全能量化扫盘", type="primary", use_container_width=True)

# ==============================================================================
# 4. 主程序
# ==============================================================================
if start_btn:
    base_list = PRESET_WATCHLISTS[selected_pool]
    if custom_tickers.strip():
        add_list = [x.strip().upper() for x in custom_tickers.replace(',', ' ').split() if x.strip()]
        watchlist = list(dict.fromkeys(base_list + add_list))
    else:
        watchlist = base_list
        
    progress_bar = st.progress(0)
    status_text = st.empty()
    all_res, diag_logs = [], []
    
    for idx, sym in enumerate(watchlist):
        status_text.text(f"正在分析 [{idx+1}/{len(watchlist)}]: {sym} ...")
        res, diag = fetch_ticker_options_safe(sym, budget, min_volume, min_oi, min_bid, min_annual_return, min_dte, max_dte, avoid_earn)
        all_res.extend(res)
        diag_logs.append(diag)
        progress_bar.progress((idx + 1) / len(watchlist))
        time.sleep(0.3)
        
    status_text.empty()
    progress_bar.empty()
    st.session_state['scan_df'] = pd.DataFrame(all_res).sort_values(by="综合评分", ascending=False).reset_index(drop=True) if all_res else pd.DataFrame()
    st.session_state['diag_logs'] = pd.DataFrame(diag_logs)

if 'scan_df' in st.session_state:
    df = st.session_state['scan_df']
    
    if not df.empty:
        fig = px.scatter(
            df, x="安全边际(%)", y="年化收益率(%)", color="财报预警", size="单张权利金($)", hover_name="代码",
            hover_data=["到期日", "行权价($)", "综合评分"], title="📊 标的收益与风险分布 (气泡大小代表权利金)",
            color_discrete_map={"✅ 安全": "#10B981", "⚠️ 跨财报": "#EF4444"}, template="plotly_dark"
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown('<div class="sub-title">💰 拟合组合现金流计算器</div>', unsafe_allow_html=True)
        st.write("选择准备同时操作的 Sell Put 组合:")
        selected_options = st.multiselect(" ", df["Unique_ID"].tolist(), label_visibility="collapsed")
        
        total_margin, total_premium = 0.0, 0.0
        if selected_options:
            selected_df = df[df["Unique_ID"].isin(selected_options)]
            total_margin = selected_df["需保证金($)"].sum()
            total_premium = selected_df["单张权利金($)"].sum()
            
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"""<div class="metric-card"><div class="metric-label">🔒 选中组合需要资金 (总保证金)</div><div class="metric-value">${total_margin:,.2f}</div></div>""", unsafe_allow_html=True)
        c2.markdown(f"""<div class="metric-card"><div class="metric-label">💵 即刻落袋权利金 (现金流)</div><div class="metric-value" style="color:#F59E0B;">${total_premium:,.2f}</div></div>""", unsafe_allow_html=True)
        c3.markdown(f"""<div class="metric-card"><div class="metric-label">📦 包含标的数量</div><div class="metric-value" style="color:#3B82F6;">{len(selected_options)} 只标的</div></div>""", unsafe_allow_html=True)

        # 独立展示选中的 Moomoo 代码，自带一键复制按钮
        if selected_options:
            st.markdown("##### 📝 已选合约 Moomoo 实盘代码 (点击右侧图标一键复制)")
            for _, row in selected_df.iterrows():
                st.code(row["Moomoo 代码"], language="text")

        st.markdown('<div class="sub-title">📋 详细数据与实盘挂单指南 (16项核心指标)</div>', unsafe_allow_html=True)
        
        df_display = df.rename(columns={"标的现价": "现价($)"}).drop(columns=["Unique_ID"])
        st.dataframe(df_display, use_container_width=True, height=600)
        
        csv = df_display.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 导出实盘数据为 CSV", data=csv, file_name=f"SellPut_Terminal_{datetime.date.today()}.csv", mime="text/csv")
        
    else:
        st.warning("🤖 未扫描到符合条件的合约，请尝试调低年化收益率或放宽到期日限制。")

    if 'diag_logs' in st.session_state:
        with st.expander("🛠️ 系统底层接口抓取与诊断日志", expanded=False):
            st.dataframe(st.session_state['diag_logs'], use_container_width=True)
else:
    st.info("👈 请在左侧配置参数，点击【🚀 启动 4.0 全能量化扫盘】。")
