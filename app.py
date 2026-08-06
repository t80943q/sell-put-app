import time
import datetime
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf

# ==============================================================================
# 1. 核心数据引擎 (16项核心诉求 + Moomoo 一键下单指令 + 财报避险)
# ==============================================================================
@st.cache_data(ttl=600, show_spinner=False)
def fetch_ticker_options_safe(symbol, budget, min_vol, min_open_int, min_b_price, min_ann_ret, min_d, max_d, avoid_earn):
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
            try:
                hist = ticker.history(period="1d")
                if not hist.empty:
                    current_price = float(hist['Close'].iloc[-1])
            except Exception:
                pass

        if not current_price or np.isnan(current_price):
            diag["HTTP状态"] = "报错/空数据"
            diag["排查结论"] = "❌ 无法获取最新股价"
            return records, diag

        diag["HTTP状态"] = "200 (正常)"
        diag["抓取现价"] = f"${current_price:.2f}"
        
        # 2. 价格区间过滤 ($2.0 ~ 预算允许最高上限的1.35倍)
        max_allowed_price = (budget / 100.0) * 1.35
        if current_price < 2.0 or current_price > max_allowed_price:
            diag["排查结论"] = f"⚠️ 现价 (${current_price:.2f}) 超出预算允许匹配区间 ($2.0 ~ ${max_allowed_price:.1f})"
            return records, diag

        # 3. 提取期权到期日
        dates = ()
        try:
            dates = ticker.options
        except Exception as e:
            diag["排查结论"] = f"❌ 期权链拉取失败: {str(e)}"
            return records, diag

        if not dates:
            diag["排查结论"] = "❌ 雅虎未返回可用期权到期日"
            return records, diag
            
        diag["可用到期日"] = len(dates)

        # 4. 提取财报日 (防黑天鹅)
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
                next_earnings_date = None

        today = datetime.date.today()
        max_strike_price = budget / 100.0

        # 5. 遍历到期日，拉取 Put 链
        for d_str in dates:
            try:
                exp_date = datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            dte = (exp_date - today).days

            # DTE 时间窗过滤
            if not (min_d <= dte <= max_d):
                continue

            # 财报避险过滤逻辑
            is_cross_earnings = False
            if next_earnings_date:
                if today < next_earnings_date <= exp_date:
                    is_cross_earnings = True
                    if avoid_earn:
                        continue

            try:
                opt_chain = ticker.option_chain(d_str)
                puts = opt_chain.puts
                if puts is None or puts.empty:
                    continue

                # 数据清洗与防空值截断
                puts['volume'] = puts['volume'].fillna(0)
                puts['openInterest'] = puts['openInterest'].fillna(0)
                puts['bid'] = puts['bid'].fillna(0.0)
                puts['ask'] = puts['ask'].fillna(0.0)

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
                    ask = float(row['ask']) if row['ask'] > 0 else bid
                    spread = round(ask - bid, 2)
                    volume = int(row['volume'])
                    open_interest = int(row['openInterest'])
                    
                    iv = float(row['impliedVolatility']) if 'impliedVolatility' in row and not pd.isna(row['impliedVolatility']) else 0.0
                    # 尝试获取 delta，如果没有这个字段则默认为 0
                    delta = float(row['delta']) if 'delta' in row and not pd.isna(row['delta']) else 0.0

                    if dte <= 0:
                        continue

                    # 核心计算：年化收益与安全边际
                    annual_return = (bid / strike) * (365.0 / dte) * 100.0
                    if annual_return < min_ann_ret:
                        continue

                    margin_required = strike * 100.0
                    premium_collected = bid * 100.0
                    safety_buffer = ((current_price - strike) / current_price) * 100.0

                    # 格式化 Moomoo 代码 (如 US.RIOT260821P00015000)
                    yymmdd = exp_date.strftime("%y%m%d")
                    strike_int = int(round(strike * 1000))
                    moomoo_code = f"US.{symbol}{yymmdd}P{strike_int:08d}"
                    moomoo_order_str = f"Sell Put {symbol} {d_str} 行权价${strike:.1f} @ Bid ${bid:.2f}"

                    # 16 项核心诉求字段整合
                    records.append({
                        "代码": symbol,
                        "Moomoo 代码 (双击复制)": moomoo_code,
                        "标的现价 ($)": round(current_price, 2),
                        "到期日": d_str,
                        "DTE (天)": dte,
                        "行权价 ($)": strike,
                        "安全边际 (%)": round(safety_buffer, 2),
                        "买一价 Bid ($)": bid,
                        "卖一价 Ask ($)": ask,
                        "买卖价差 ($)": spread,
                        "权利金/单张 ($)": round(premium_collected, 2),
                        "保证金/单笔 ($)": round(margin_required, 2),
                        "年化收益率 (%)": round(annual_return, 2),
                        "隐含波动率 IV (%)": f"{iv * 100:.1f}%",
                        "Delta": round(delta, 3) if delta != 0 else "N/A",
                        "成交量 (张)": volume,
                        "持仓量 (张)": open_interest,
                        "Moomoo 下单指令": moomoo_order_str,
                        "跨财报风险": "⚠️ 跨财报" if is_cross_earnings else "✅ 安全"
                    })

            except Exception as opt_err:
                err_str = str(opt_err)
                if "Rate limited" in err_str or "429" in err_str:
                    diag["排查结论"] = "⚠️ 触发雅虎频控 (Rate Limited)"
                    break
                continue

        diag["符合条件合约数"] = len(records)
        if len(records) > 0:
            diag["排查结论"] = "✅ 扫描成功"
        else:
            diag["排查结论"] = f"⚠️ 找到 {len(dates)} 个到期日，无符合设定的合约 (建议关闭财报避险或调低年化门槛)"

    except Exception as e:
        diag["排查结论"] = f"❌ 运行异常: {str(e)}"

    return records, diag

# ==============================================================================
# 2. UI 界面与侧边栏控制
# ==============================================================================
st.set_page_config(page_title="Sell Put 智能量化终端 4.0", page_icon="🚀", layout="wide")

st.markdown('<h2 style="color:#1E293B;">🚀 Sell Put 智能量化终端 4.0 (16项核心诉求·Moomoo臻选版)</h2>', unsafe_allow_html=True)

# 完整恢复了四大预设股票池，包含 ETF 列表
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
    "稳健指数 & 行业 ETF (ETF Wheel Strategy)": [
        'TQQQ', 'SOXL', 'IWM', 'QQQ', 'SPY', 'ARKK', 'KWEB', 'XLE', 'XLF', 'SMH', 'TLT', 'GDX'
    ]
}

with st.sidebar:
    st.header("⚙️ 16项策略风控面板")
    
    selected_pool_name = st.selectbox("📋 股票池预设选择", list(PRESET_WATCHLISTS.keys()), index=0)
    custom_tickers_input = st.text_area("✍️ 补充自定义代码 (用逗号或空格分隔)", value="", placeholder="例如: AMD, INTC, BAC")
    
    st.markdown("---")
    st.subheader("💰 资金与预算配置")
    budget = st.number_input("💵 单笔预算上限 ($)", value=3500, step=500, min_value=500, help="做ETF时建议调高此预算至 6000-10000 以上")
    
    st.markdown("---")
    st.subheader("🌊 盘口风控")
    min_volume = st.number_input("最低成交量 (张)", value=0, min_value=0, help="非交易时间建议设为0")
    min_oi = st.number_input("最低持仓量 (张)", value=0, min_value=0)
    min_bid = st.number_input("最低买一价 (Bid $)", value=0.02, step=0.01, format="%.2f")
    min_annual_return = st.number_input("最低年化收益率 (%)", value=6.0, step=0.5, help="如果找不到合约，建议调低至 5%")
    
    st.markdown("---")
    st.subheader("📅 周期与财报风控")
    min_dte = st.number_input("最小到期天数 (DTE)", value=1, min_value=1)
    max_dte = st.number_input("最大到期天数 (DTE)", value=60, min_value=7)
    earnings_avoid = st.checkbox("开启财报避险 (遇到0合约时请取消勾选)", value=False)
    
    st.markdown("---")
    if st.button("🧹 清除缓存并重新扫描"):
        st.cache_data.clear()
        st.success("缓存已清除！")
        
    start_btn = st.button("🚀 启动 4.0 全能量化扫描", type="primary", use_container_width=True)

def get_combined_watchlist():
    base_list = PRESET_WATCHLISTS[selected_pool_name]
    if custom_tickers_input.strip():
        add_list = [x.strip().upper() for x in custom_tickers_input.replace(',', ' ').split() if x.strip()]
        return list(dict.fromkeys(base_list + add_list))
    return base_list

if start_btn:
    watchlist = get_combined_watchlist()
    total_tickers = len(watchlist)
    
    st.write(f"🔍 正在对 **{total_tickers}** 只标的进行缓冲防封扫描与诊断分析...")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    all_results, diag_logs = [], []
    
    for idx, sym in enumerate(watchlist):
        status_text.text(f"正在扫描 [{idx+1}/{total_tickers}]: {sym} ...")
        
        res, diag = fetch_ticker_options_safe(
            symbol=sym, budget=budget, min_vol=min_volume, min_open_int=min_oi,
            min_b_price=min_bid, min_ann_ret=min_annual_return,
            min_d=min_dte, max_d=max_dte, avoid_earn=earnings_avoid
        )
        all_results.extend(res)
        diag_logs.append(diag)
        
        progress_bar.progress((idx + 1) / total_tickers)
        time.sleep(0.4) # 防封锁缓冲间隔
        
    status_text.empty()
    progress_bar.empty()

    if all_results:
        df_res = pd.DataFrame(all_results)
        df_res = df_res.sort_values(by="年化收益率 (%)", ascending=False).reset_index(drop=True)
        
        st.success(f"🎉 扫描完成！共获得 **{len(df_res)}** 条支持一键复制到 Moomoo 下单的策略合约组合。")
        
        # 定义需要展示在前端表格的列顺序 (将 Moomoo 代码前置方便复制)
        cols_to_show = [
            "代码", "Moomoo 代码 (双击复制)", "标的现价 ($)", "到期日", "DTE (天)", "行权价 ($)", 
            "安全边际 (%)", "买一价 Bid ($)", "卖一价 Ask ($)", "买卖价差 ($)", "权利金/单张 ($)", 
            "保证金/单笔 ($)", "年化收益率 (%)", "隐含波动率 IV (%)", "Delta", 
            "成交量 (张)", "持仓量 (张)", "Moomoo 下单指令", "跨财报风险"
        ]
        
        st.dataframe(df_res[cols_to_show], use_container_width=True)
        
        csv_data = df_res.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 导出包含 16 项完整字段 CSV",
            data=csv_data,
            file_name=f"Sell_Put_Moomoo_16Metrics_{datetime.date.today()}.csv",
            mime="text/csv"
        )
    else:
        st.warning("🤖 未找到符合要求的标的！请在侧边栏取消勾选【开启财报避险】或降低【最低年化收益率】后再试。")

    with st.expander("🛠️ 接口抓取与诊断明细 (点击展开排查原因)", expanded=True):
        st.dataframe(pd.DataFrame(diag_logs), use_container_width=True)
else:
    st.info("👈 请在侧边栏配置筛选风控参数，然后点击 **【🚀 启动 4.0 全能量化扫描】** 开始运行。")
