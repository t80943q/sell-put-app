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
st.set_page_config(page_title="期权轮子中枢 5.0 (主理人版)", page_icon="🏦", layout="wide")

st.markdown("""
<style>
    .main-title { font-size: 2.2rem; font-weight: 700; margin-bottom: 0.5rem; }
    .sub-title { font-size: 1.5rem; font-weight: 600; margin-top: 2rem; margin-bottom: 1rem; color: #EAB308;}
    .metric-card { background-color: #1E293B; padding: 15px; border-radius: 8px; text-align: center; border: 1px solid #334155; }
    .metric-value { font-size: 1.8rem; font-weight: bold; color: #10B981; }
    .metric-label { font-size: 1rem; color: #94A3B8; }
    .rec-box { background-color: #0F172A; padding: 20px; border-radius: 10px; border-left: 5px solid #3B82F6; margin-bottom: 20px;}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">🏦 Wheel Strategy 5.0 期权轮子中枢 (机构主理人版)</div>', unsafe_allow_html=True)

# --- 板块映射字典 (用于 AI 风险分散) ---
SECTOR_MAP = {
    'Crypto (加密概念)': ['RIOT', 'CLSK', 'MARA', 'COIN', 'MSTR'],
    'EV (新能源车)': ['TSLA', 'LCID', 'NIO', 'XPEV', 'F'],
    'Mega Tech (科技巨头)': ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'AMD', 'QCOM', 'INTC'],
    'Growth (高波动成长)': ['SOFI', 'HOOD', 'AFRM', 'UPST', 'RBLX', 'DKNG', 'PATH', 'SOUN', 'CVNA', 'SNOW', 'AI', 'ROKU', 'PLTR', 'SMCI', 'ARM'],
    'ETF (稳健宽基)': ['TQQQ', 'SOXL', 'IWM', 'QQQ', 'SPY', 'ARKK', 'KWEB', 'XLE', 'XLF', 'SMH', 'TLT', 'GDX']
}
def get_sector(sym):
    for sec, syms in SECTOR_MAP.items():
        if sym in syms: return sec
    return 'Other (其他)'

# ==============================================================================
# 2. 核心数据引擎 (5.0 升级: 引入 CC 模式、POP 胜率、50% 止盈、Gamma 惩罚)
# ==============================================================================
@st.cache_data(ttl=600, show_spinner=False)
def fetch_ticker_options_safe(symbol, budget, min_vol, min_open_int, min_b_price, min_ann_ret, min_d, max_d, avoid_earn, strategy="Sell Put"):
    records = []
    diag = {"代码": symbol, "HTTP状态": "未请求", "抓取现价": "N/A", "可用到期日": 0, "符合条件合约数": 0, "排查结论": "未完成"}
    
    try:
        ticker = yf.Ticker(symbol)
        
        current_price = None
        try: current_price = getattr(ticker.fast_info, 'last_price', None)
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
        
        # 预算逻辑：Sell Put 是准备接盘所需的资金；CC 是假设你持仓的价值
        max_allowed_price = (budget / 100.0) * 1.50
        if current_price > max_allowed_price:
            diag["排查结论"] = f"⚠️ 现价 (${current_price:.2f}) 太高，超出当前资金配置上限"
            return records, diag

        try: dates = ticker.options
        except Exception as e:
            diag["排查结论"] = f"❌ 期权链拉取失败"
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
        max_strike = budget / 100.0 if strategy == "Sell Put" else current_price * 1.5

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
                # 策略分流
                options_data = opt_chain.puts if strategy == "Sell Put" else opt_chain.calls
                if options_data is None or options_data.empty: continue

                df_opts = options_data.copy()
                df_opts['volume'] = df_opts['volume'].fillna(0)
                df_opts['openInterest'] = df_opts['openInterest'].fillna(0)
                df_opts['bid'] = df_opts['bid'].fillna(0.0)
                
                # OTM 虚值过滤
                if strategy == "Sell Put":
                    valid_opts = df_opts[(df_opts['strike'] <= max_strike) & (df_opts['strike'] < current_price) & 
                                      (df_opts['bid'] >= min_b_price) & (df_opts['volume'] >= min_vol) & 
                                      (df_opts['openInterest'] >= min_open_int)]
                else: # Covered Call
                    valid_opts = df_opts[(df_opts['strike'] > current_price) & 
                                      (df_opts['bid'] >= min_b_price) & (df_opts['volume'] >= min_vol) & 
                                      (df_opts['openInterest'] >= min_open_int)]

                for _, row in valid_opts.iterrows():
                    strike, bid = float(row['strike']), float(row['bid'])
                    ask = float(row['ask']) if not pd.isna(row.get('ask')) and row['ask'] > 0 else bid
                    iv = float(row['impliedVolatility']) if not pd.isna(row.get('impliedVolatility')) else 0.0
                    delta = float(row['delta']) if not pd.isna(row.get('delta')) else 0.0

                    if dte <= 0: continue
                    
                    # 占用的资金 (Put是行权价*100，Call是现价*100代表持仓市值)
                    margin = strike * 100.0 if strategy == "Sell Put" else current_price * 100.0
                    premium = bid * 100.0
                    
                    # 静态年化
                    annual_return = (premium / margin) * (365.0 / dte) * 100.0
                    if annual_return < min_ann_ret: continue

                    # 安全边际 (缓冲空间)
                    if strategy == "Sell Put":
                        safety_buf = ((current_price - strike) / current_price) * 100.0
                    else:
                        safety_buf = ((strike - current_price) / current_price) * 100.0

                    # 胜率估算 POP (基于 Delta)
                    pop = (100.0 - abs(delta * 100.0)) if delta != 0 else np.nan

                    # 50% 止盈目标价
                    target_tp = bid * 0.5

                    # 综合评分 (惩罚末日期权 Gamma 风险)
                    base_score = annual_return + safety_buf + (pop * 0.1 if not pd.isna(pop) else 5.0)
                    score = round(base_score * 0.5 if dte < 7 else base_score, 2)

                    yymmdd = exp_date.strftime("%y%m%d")
                    type_char = 'P' if strategy == "Sell Put" else 'C'
                    moomoo_code = f"US.{symbol}{yymmdd}{type_char}{int(round(strike * 1000)):08d}"
                    unique_id = f"[{symbol}] 到期:{d_str} | 行权价:${strike} | 权利金:${premium:.2f} (年化:{annual_return:.1f}%)"
                    cmd_str = f"Sell {type_char}ut {symbol} {d_str} Strike ${strike} @ Bid ${bid}" if strategy == "Sell Put" else f"Sell {type_char}all {symbol} {d_str} Strike ${strike} @ Bid ${bid}"

                    records.append({
                        "Unique_ID": unique_id,
                        "代码": symbol,
                        "所属板块": get_sector(symbol),
                        "综合评分": score,
                        "现价($)": current_price,
                        "到期日": d_str,
                        "DTE(天)": dte,
                        "行权价($)": strike,
                        "需资金($)": round(margin, 2),
                        "安全边际(%)": round(safety_buf, 2),
                        "胜率(POP)": f"{pop:.1f}%" if not pd.isna(pop) else "N/A",
                        "挂单Bid($)": bid,
                        "单张入账($)": premium,
                        "50%止盈价": f"${target_tp:.2f}",
                        "年化收益(%)": round(annual_return, 2),
                        "IV环境": f"{iv*100:.1f}%",
                        "Delta": round(delta, 3) if delta != 0 else "N/A",
                        "成交量": int(row['volume']),
                        "持仓量": int(row['openInterest']),
                        "跨财报": "⚠️ 是" if is_cross_earnings else "✅ 否",
                        "Moomoo 代码": moomoo_code,
                        "实盘指令": cmd_str
                    })
            except: continue

        diag["符合条件合约数"] = len(records)
        diag["排查结论"] = "✅ 扫描成功" if records else f"⚠️ 找到 {len(dates)} 个到期日，全被过滤 (建议放宽限制)"
    except Exception as e:
        diag["排查结论"] = f"❌ 运行异常: {str(e)}"
    return records, diag

# ==============================================================================
# 3. 侧边栏交互
# ==============================================================================
all_tickers_combined = list(dict.fromkeys(sum(SECTOR_MAP.values(), [])))
PRESET_WATCHLISTS = {"🔥 全矩阵综合池 (All-in-One)": all_tickers_combined}
PRESET_WATCHLISTS.update(SECTOR_MAP)

with st.sidebar:
    st.header("⚙️ 轮子闭环与策略风控")
    
    # 5.0 新增：策略切换
    trade_strategy = st.radio("🔄 选择操作模式", ["Sell Put (赚现金流/准备接盘)", "Covered Call (已有持仓/抛售赚息)"])
    strategy_val = "Sell Put" if "Put" in trade_strategy else "Covered Call"
    
    account_capital = st.number_input("🏦 账户可用总资金/股票总值 ($)", value=3400, step=100)
    
    st.markdown("---")
    selected_pool = st.selectbox("📋 股票池选择", list(PRESET_WATCHLISTS.keys()), index=0)
    custom_tickers = st.text_area("✍️ 自定义代码 (空格/逗号分隔)", value="")
    
    budget = st.number_input("💵 单笔资金上限/持仓限额 ($)", value=3400, step=500)
    min_volume = st.number_input("最低成交量 (张)", value=0, step=1)
    min_oi = st.number_input("最低持仓量 (张)", value=0, step=10)
    min_bid = st.number_input("最低买一价 (Bid $)", value=0.01, step=0.01) 
    min_annual_return = st.number_input("最低年化收益率 (%)", value=3.0, step=0.5) 
    
    st.markdown("---")
    min_dte = st.number_input("最小到期天数 (DTE)", value=1, min_value=1)
    max_dte = st.number_input("最大到期天数 (DTE)", value=180, min_value=1)
    avoid_earn = st.checkbox("开启财报避险 (隐藏跨财报期权)", value=False)
    
    if avoid_earn and max_dte > 80:
        st.warning("⚠️ **策略预警**：做 >90 天长周期时开启避险会导致远期合约全部阵亡，建议取消。")
    
    st.markdown("---")
    if st.button("🧹 清理系统缓存"): 
        st.cache_data.clear()
        if 'scan_df' in st.session_state: del st.session_state['scan_df']
        if 'diag_logs' in st.session_state: del st.session_state['diag_logs']
        st.success("缓存已清除！")
        
    start_btn = st.button(f"🚀 启动 {strategy_val} 全能扫盘", type="primary", use_container_width=True)

# ==============================================================================
# 4. 主程序运行
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
        res, diag = fetch_ticker_options_safe(sym, budget, min_volume, min_oi, min_bid, min_annual_return, min_dte, max_dte, avoid_earn, strategy=strategy_val)
        all_res.extend(res)
        diag_logs.append(diag)
        progress_bar.progress((idx + 1) / len(watchlist))
        time.sleep(0.3)
        
    status_text.empty()
    progress_bar.empty()
    st.session_state['scan_df'] = pd.DataFrame(all_res).sort_values(by="综合评分", ascending=False).reset_index(drop=True) if all_res else pd.DataFrame()
    st.session_state['diag_logs'] = pd.DataFrame(diag_logs)
    st.session_state['cur_strategy'] = strategy_val

if 'scan_df' in st.session_state:
    df = st.session_state['scan_df']
    current_strat = st.session_state.get('cur_strategy', 'Sell Put')
    
    if not df.empty:
        # --- 1. AI 智能仓位推荐系统 (5.0 板块隔离强化版) ---
        st.markdown(f'<div class="sub-title">🤖 机构级 AI 智能仓位分配 (当前策略: {current_strat})</div>', unsafe_allow_html=True)
        
        rec_list = []
        rem_cap = account_capital
        seen_sectors = set() # 核心：用于记录已选中的板块
        
        for _, row in df.iterrows():
            margin = row["需资金($)"]
            sec = row["所属板块"]
            # 条件：资金够用，且该板块还没有被挑过，达到强分散效果
            if margin <= rem_cap and sec not in seen_sectors:
                rec_list.append(row)
                rem_cap -= margin
                seen_sectors.add(sec)
                
        if rec_list:
            rec_df = pd.DataFrame(rec_list)
            used_margin = rec_df["需资金($)"].sum()
            total_prem = rec_df["单张入账($)"].sum()
            avg_ann = rec_df["年化收益(%)"].mean()
            
            st.markdown(f"""
            <div class="rec-box">
                <h4 style='color: white; margin-top:0;'>💡 基于 ${account_capital:,.2f} 预算与 <b>板块风险隔离原则</b>，推导出的最稳组合：</h4>
                <p style='color: #94A3B8; font-size: 1.1rem;'>
                    ✔️ <b>实际占用资金：</b> <span style='color:#10B981;'>${used_margin:,.2f}</span> &nbsp;&nbsp;|&nbsp;&nbsp; 
                    💰 <b>预计落袋现金：</b> <span style='color:#F59E0B;'>${total_prem:,.2f}</span> &nbsp;&nbsp;|&nbsp;&nbsp; 
                    📈 <b>组合平均年化：</b> <span style='color:#3B82F6;'>{avg_ann:.1f}%</span>
                </p>
                <small style='color: gray;'><i>* 已启动板块相关性隔离，有效规避同涨同跌的系统性 Gamma 风险。</i></small>
            </div>
            """, unsafe_allow_html=True)
            
            for _, row in rec_df.iterrows():
                st.markdown(f"**🎯 {row['代码']}**  <small style='color:gray;'>[{row['所属板块']}]</small> | 胜率: **{row['胜率(POP)']}** | DTE: **{row['DTE(天)']}天** | 建议在 **{row['50%止盈价']}** 挂单止盈买回", unsafe_allow_html=True)
                c_m, c_i = st.columns(2)
                with c_m: st.code(row["Moomoo 代码"], language="text")
                with c_i: st.code(row["实盘指令"], language="text")
        else:
            st.info(f"🤖 预算 (${account_capital}) 过低，无法匹配任何高分标的。")

        # --- 2. 交互式散点图 ---
        st.markdown("---")
        fig = px.scatter(
            df, x="安全边际(%)", y="年化收益(%)", color="所属板块", size="单张入账($)", hover_name="代码",
            hover_data=["到期日", "行权价($)", "胜率(POP)"], 
            title="📊 标的收益与风险分布散点图 (寻找性价比孤岛)", template="plotly_dark"
        )
        st.plotly_chart(fig, use_container_width=True)

        # --- 3. 手动现金流组合器 ---
        st.markdown('<div class="sub-title">💰 手动组合拟合面板</div>', unsafe_allow_html=True)
        selected_options = st.multiselect("自定义搭配策略:", df["Unique_ID"].tolist(), label_visibility="collapsed")
        
        total_margin, total_premium = 0.0, 0.0
        if selected_options:
            selected_df = df[df["Unique_ID"].isin(selected_options)]
            total_margin = selected_df["需资金($)"].sum()
            total_premium = selected_df["单张入账($)"].sum()
            
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"""<div class="metric-card"><div class="metric-label">🔒 需占用资金</div><div class="metric-value">${total_margin:,.2f}</div></div>""", unsafe_allow_html=True)
        c2.markdown(f"""<div class="metric-card"><div class="metric-label">💵 即刻现金流</div><div class="metric-value" style="color:#F59E0B;">${total_premium:,.2f}</div></div>""", unsafe_allow_html=True)
        c3.markdown(f"""<div class="metric-card"><div class="metric-label">📦 合约数量</div><div class="metric-value" style="color:#3B82F6;">{len(selected_options)} 笔</div></div>""", unsafe_allow_html=True)

        if selected_options:
            st.markdown("##### 📝 自选组合实盘挂单详情")
            for _, row in selected_df.iterrows():
                st.markdown(f"**{row['代码']}** (胜率 POP: {row['胜率(POP)']} | 建议止盈价: {row['50%止盈价']})")
                c_m2, c_i2 = st.columns(2)
                with c_m2: st.code(row["Moomoo 代码"], language="text")
                with c_i2: st.code(row["实盘指令"], language="text")

        # --- 4. 详细数据表 ---
        st.markdown(f'<div class="sub-title">📋 详细数据底表 (已集成 50%止盈 与 POP胜率)</div>', unsafe_allow_html=True)
        
        df_display = df.drop(columns=["Unique_ID"])
        st.dataframe(df_display, use_container_width=True, height=600)
        
        csv = df_display.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 导出实盘数据 CSV", data=csv, file_name=f"Wheel_Strategy_V5_{datetime.date.today()}.csv", mime="text/csv")
        
    else:
        st.warning("🤖 未扫描到符合条件的合约。")

    if 'diag_logs' in st.session_state:
        with st.expander("🛠️ 系统抓取日志与底层诊断 (点击展开)"):
            st.dataframe(st.session_state['diag_logs'], use_container_width=True)
else:
    st.info("👈 请配置资金，点击【🚀 启动全能扫盘】。")
