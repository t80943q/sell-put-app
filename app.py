import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import requests
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem

# ==============================================================================
# 1. 核心反爬/伪装机制 (骗过 Yahoo Finance 频控与 Cloud IP 拦截)
# ==============================================================================
@st.cache_resource(ttl=1800)
def get_bypass_session():
    """生成具备动态 Header 伪装能力的 requests Session 以绕过 Yahoo Finance 限制"""
    session = requests.Session()
    
    # 随机生成真实的现代浏览器 User-Agent (Chrome / Edge on Windows / Mac)
    software_names = [SoftwareName.CHROME.value, SoftwareName.EDGE.value]
    operating_systems = [OperatingSystem.WINDOWS.value, OperatingSystem.MAC.value]
    user_agent_rotator = UserAgent(software_names=software_names, operating_systems=operating_systems, limit=100)
    
    random_ua = user_agent_rotator.get_random_user_agent()
    
    session.headers.update({
        'User-Agent': random_ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Referer': 'https://finance.yahoo.com/',
    })
    return session

bypass_session = get_bypass_session()

# ==============================================================================
# 2. 页面全局配置与 UI 样式设定
# ==============================================================================
st.set_page_config(
    page_title="Sell Put 智能量化终端 4.0",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义 css 样式调整
st.markdown("""
<style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0.5rem;
    }
    .sub-title {
        font-size: 1rem;
        color: #64748B;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 12px;
        text-align: center;
    }
    .stDataFrame {
        border-radius: 8px;
        overflow: hidden;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">🚀 Sell Put 智能量化终端 4.0 (老股民实盘臻选·黄金平衡版)</div>', unsafe_allow_html=True)

# ==============================================================================
# 3. 股票池内置预设
# ==============================================================================
PRESET_WATCHLISTS = {
    "核心臻选优质标的池 (默认推荐)": [
        'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AMD', 'INTC', 'PLTR',
        'QCOM', 'NFLX', 'BABA', 'PDD', 'DIS', 'BA', 'COIN', 'MARA', 'NIO', 'XPEV',
        'AMD', 'UBER', 'ABNB', 'SQ', 'PYPL', 'SHOP', 'HOOD', 'SOFI', 'SMCI', 'ARM'
    ],
    "科技巨头 & 高流动性 (Mega Tech)": [
        'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'COST', 'ORCL'
    ],
    "高波动率 / 高年化收益 (High IV Growth)": [
        'PLTR', 'COIN', 'MARA', 'RIOT', 'SMCI', 'ARM', 'HOOD', 'SOFI', 'DKNG', 'U',
        'AFRM', 'UPST', 'IONQ', 'RBLX', 'MSTR', 'CVNA', 'SNOW', 'PATH', 'AI', 'ROKU'
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
    budget = st.number_input("💵 单笔预算上限 ($)", value=5000, step=500, min_value=500)
    
    max_strike = budget / 100.0
    st.info(f"🎯 **预算 ${budget:,} 对应风控区间：**

"
            f"* **最大行权价上限:** ${max_strike:.2f}
"
            f"* **动态匹配股价区间:** $2.0 ~ ${max_strike * 1.35:.1f}")
    
    st.markdown("---")
    st.subheader("🌊 盘口与买方对手盘风控")
    min_volume = st.number_input("最低成交量 (张)", value=5, step=1, min_value=0)
    min_oi = st.number_input("最低持仓量 (张)", value=5, step=1, min_value=0)
    min_bid = st.number_input("最低买一价 (Bid $)", value=0.02, step=0.01, min_value=0.01, format="%.2f")
    min_annual_return = st.number_input("最低年化收益率 (%)", value=12.0, step=1.0, min_value=0.0)
    
    st.markdown("---")
    st.subheader("📅 到期日与财报风控")
    min_dte = st.number_input("最小到期天数 (DTE)", value=7, step=1, min_value=1)
    max_dte = st.number_input("最大到期天数 (DTE)", value=60, step=5, min_value=7)
    
    earnings_avoid = st.checkbox("开启财报避险 (隐藏跨财报期权)", value=True, help="剔除在期权到期日前即将发布财报的股票，降低剧烈波动风险")
    
    st.markdown("---")
    start_btn = st.button("🚀 启动 4.0 全能量化扫描", type="primary", use_container_width=True)

# ==============================================================================
# 5. 数据抓取与解析引擎 (支持伪装 Header 与错误容忍)
# ==============================================================================
def get_combined_watchlist():
    base_list = PRESET_WATCHLISTS[selected_pool_name]
    if custom_tickers_input.strip():
        add_list = [x.strip().upper() for x in custom_tickers_input.replace(',', ' ').split() if x.strip()]
        combined = list(dict.fromkeys(base_list + add_list)) # 去重且保持顺序
    else:
        combined = base_list
    return combined

def fetch_earnings_date(ticker_obj):
    """获取标的下一期财报日期"""
    try:
        calendar = ticker_obj.calendar
        if isinstance(calendar, dict) and 'Earnings Date' in calendar:
            e_dates = calendar['Earnings Date']
            if e_dates and len(e_dates) > 0:
                return pd.to_datetime(e_dates[0]).date()
        elif isinstance(calendar, pd.DataFrame) and not calendar.empty:
            if 'Earnings Date' in calendar.index:
                e_dates = calendar.loc['Earnings Date'].values
                if len(e_dates) > 0:
                    return pd.to_datetime(e_dates[0]).date()
    except Exception:
        pass
    return None

def analyze_ticker_options(symbol, budget, min_vol, min_open_int, min_b_price, min_ann_ret, min_d, max_d, avoid_earn):
    """单只股票的深度 Sell Put 筛选算法"""
    records = []
    try:
        # 使用伪装 Session 实例化 yfinance Ticker，防封锁
        ticker = yf.Ticker(symbol, session=bypass_session)
        
        # 获取当前现价
        fast_info = ticker.fast_info
        current_price = getattr(fast_info, 'last_price', None)
        if current_price is None or np.isnan(current_price) or current_price <= 0:
            # 备用方案：通过 history 兜底获取最新价
            hist = ticker.history(period="2d")
            if not hist.empty:
                current_price = float(hist['Close'].iloc[-1])
            else:

                return records

        # 股价区间筛选 (现价不能高于预算可承受上限的 1.35 倍，且不低于 2.0 美元)
        max_allowed_price = (budget / 100.0) * 1.35
        if current_price < 2.0 or current_price > max_allowed_price:
            return records

        # 获取期权链到期日列表
        dates = ticker.options
        if not dates:
            return records

        # 获取财报日
        next_earnings_date = None
        if avoid_earn:
            next_earnings_date = fetch_earnings_date(ticker)

        today = datetime.date.today()

        for d_str in dates:
            exp_date = datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days

            # DTE 时间窗过滤
            if dte < min_d or dte > max_d:
                continue

            # 财报避险过滤
            if avoid_earn and next_earnings_date:
                if today < next_earnings_date <= exp_date:
                    continue # 跨财报期，避险跳过

            # 抓取 Put 期权链
            try:
                opt_chain = ticker.option_chain(d_str)
                puts = opt_chain.puts
                if puts.empty:
                    continue
            except Exception:
                continue

            # 行权价上限约束 (绝对不能超过 预算/100)
            max_strike_price = budget / 100.0
            
            # 筛选符合条件的行权价与流动性指标
            valid_puts = puts[
                (puts['strike'] <= max_strike_price) &
                (puts['strike'] < current_price) &  # 仅 Sell OTM Put (虚值期权)
                (puts['bid'] >= min_b_price) &
                (puts['volume'] >= min_vol) &
                (puts['openInterest'] >= min_open_int)
            ]

            for _, row in valid_puts.iterrows():
                strike = float(row['strike'])
                bid = float(row['bid'])
                ask = float(row['ask'])
                mid = round((bid + ask) / 2.0, 2)
                volume = int(row['volume']) if not pd.isna(row['volume']) else 0
                open_interest = int(row['openInterest']) if not pd.isna(row['openInterest']) else 0
                iv = float(row['impliedVolatility']) if 'impliedVolatility' in row and not pd.isna(row['impliedVolatility']) else 0.0

                # 资金与收益计算
                margin_required = strike * 100.0  # 单张合约所需现金担保 ($)
                premium_collected = bid * 100.0   # 按 Bid 单张收取的权利金 ($)
                
                # 年化收益率计算公式： (Bid / Strike) * (365 / DTE) * 100
                annual_return = (bid / strike) * (365.0 / dte) * 100.0
                
                # 安全边际 (行权价距离现价的折价百分比)
                safety_buffer = ((current_price - strike) / current_price) * 100.0

                if annual_return < min_ann_ret:
                    continue

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

    except Exception:
        pass

    return records

# ==============================================================================
# 6. 主逻辑渲染与表格/导出展示
# ==============================================================================
if start_btn:
    watchlist = get_combined_watchlist()
    total_tickers = len(watchlist)
    
    st.write(f"🔍 正在对 **{total_tickers}** 只标的进行多维度深度扫描与反爬防封数据拉取...")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    all_results = []
    
    for idx, sym in enumerate(watchlist):
        status_text.text(f"正在扫描 [{idx+1}/{total_tickers}]: {sym} ...")
        res = analyze_ticker_options(
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
        progress_bar.progress((idx + 1) / total_tickers)
        
    status_text.empty()
    progress_bar.empty()

    if all_results:
        df_res = pd.DataFrame(all_results)
        
        # 按年化收益率降序排列
        df_res = df_res.sort_values(by="年化收益率", ascending=False).reset_index(drop=True)
        
        # 格式化展示年化收益率
        df_display = df_res.copy()
        df_display["年化收益率"] = df_display["年化收益率"].apply(lambda x: f"{x:.2f}%")
        
        # 顶部 KPI 指标看板
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("符合条件标的数", len(df_res["代码"].unique()))
        with c2:
            st.metric("精选 Put 策略总数", len(df_res))
        with c3:
            max_ret = df_res["年化收益率"].max()
            st.metric("最高年化收益率", f"{max_ret:.2f}%")
        with c4:
            avg_ret = df_res["年化收益率"].mean()
            st.metric("平均年化收益率", f"{avg_ret:.2f}%")

        st.success(f"🎉 扫描完成！共找到 **{len(df_res)}** 条符合风控策略的 Sell Put 合约组合。")
        
        # 主表格展示
        st.dataframe(
            df_display,
            use_container_width=True,
            column_config={
                "年化收益率": st.column_config.TextColumn("年化收益率", help="按 Bid 计算的年化复合预估收益"),
                "安全边际": st.column_config.TextColumn("安全边际", help="(现价 - 行权价) / 现价"),
            }
        )
        
        # 下载 CSV 导出功能
        csv_data = df_res.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 导出筛选结果为 CSV 文件",
            data=csv_data,
            file_name=f"Sell_Put_Scan_{datetime.date.today()}.csv",
            mime="text/csv",
            type="secondary"
        )
    else:
        st.warning(
            f"🤖 未找到符合要求的标的！

"
            f"💡 **建议调整参数再次尝试：**
"
            f"1. 适当调大侧边栏 **【单笔预算上限】**（例如调至 $5,000 ~ $10,000）
"
            f"2. 降低 **【最低成交量】** 或 **【最低持仓量】**（例如调至 1 ~ 5）
"
            f"3. 调低 **【最低年化收益率】**（例如调至 8% ~ 10%）
"
            f"4. 切换顶部 **【股票池预设】** 或在自定义框中输入更多高波动性标的（如 PLTR, COIN, TSLA）。"
        )
else:
    # 默认提示界面
    st.info("👈 请在侧边栏配置筛选风控参数，然后点击 **【🚀 启动 4.0 全能量化扫描】** 开始运行。")
