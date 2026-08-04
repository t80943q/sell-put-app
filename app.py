import time
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Sell Put 终极量化终端 3.0", layout="wide")
st.title("🚀 Sell Put 智能量化终端 3.0 (含热门 ETF 专区)")

# ================= 侧边栏风控设置 =================
st.sidebar.header("⚙️ 筛选风控与预算")
min_price = st.sidebar.number_input("最低股价 ($)", value=5.0, step=1.0)
max_price = st.sidebar.number_input("最高股价 ($)", value=150.0, step=5.0)
max_budget = st.sidebar.number_input("单笔预算上限 ($)", value=15000, step=500)
min_volume = st.sidebar.number_input("最低成交量", value=5, step=1)
min_open_interest = st.sidebar.number_input("最低持仓量", value=20, step=5)

btn_scan = st.sidebar.button("🚀 启动 3.0 全能量化扫盘", type="primary")

# 🌟 43 个高流动性热门美股个股 + 核心 ETF 标的池
target_pool = [
    # 🆕 8 个适合做 Sell Put 的核心 ETF 标的
    "SOXL",  # 3倍做多半导体（高波动高年化）
    "TQQQ",  # 3倍做多纳斯达克100
    "LABU",  # 3倍做多生物医药
    "IWM",   # 罗素2000小盘股大盘
    "QQQ",   # 纳斯达克100大盘
    "SPY",   # 标普500大盘
    "TLT",   # 20年期+美国国债
    "SLV",   # 白银ETF
    # 热门个股
    "PLTR",
    "HOOD",
    "SOFI",
    "F",
    "NIO",
    "RIVN",
    "INTC",
    "BAC",
    "AAL",
    "MARA",
    "AMC",
    "LCID",
    "CCL",
    "PFE",
    "GRAB",
    "VALE",
    "SNAP",
    "DKNG",
    "WFC",
    "PBR",
    "COIN",
    "PYPL",
    "BABA",
    "PDD",
    "JD",
    "NVDA",
    "TSLA",
    "AMD",
    "SQ",
    "UBER",
    "RIOT",
    "AFRM",
    "SMCI",
    "CLSK",
    "UPST",
]


# 辅助函数：检测财报日预警 (ETF 无财报，会自动标记安全)
def check_earnings_warning(ticker_obj, expiry_str):
    try:
        cal = ticker_obj.calendar
        if cal is not None and not cal.empty:
            if isinstance(cal, dict) and "Earnings Date" in cal:
                earnings_dates = cal["Earnings Date"]
            elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
                earnings_dates = cal.loc["Earnings Date"].tolist()
            else:
                return "✅ 安全(无预警)"

            expiry_dt = pd.to_datetime(expiry_str).tz_localize(None)
            today = pd.Timestamp.now().normalize()

            for ed in earnings_dates:
                ed_dt = pd.to_datetime(ed).tz_localize(None)
                if today <= ed_dt <= expiry_dt:
                    return f"⚠️ 财报日({ed_dt.strftime('%m-%d')})"
    except Exception:
        pass
    return "✅ 安全(无预警)"


def scan_options_v3():
    st.info("🤖 正在为您全网扫描、分析财报风险并计算最佳挂单价...")
    progress_bar = st.progress(0)
    final_symbols = []

    # 1. 股价区间过滤
    total_stocks = len(target_pool)
    for idx, sym in enumerate(target_pool):
        progress_bar.progress((idx + 1) / total_stocks)
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="1d")
            if not hist.empty:
                p = hist["Close"].iloc[-1]
                if min_price <= p <= max_price:
                    final_symbols.append((sym, p, t))
        except Exception:
            continue

    progress_bar.empty()

    if not final_symbols:
        st.warning(
            "⚠️ 未找到在当前 [最低/最高股价] 区间内的股票，请调宽侧边栏的股价范围。"
        )
        return

    all_opportunities = []

    # 2. 期权链深度分析
    for symbol, current_price, ticker_obj in final_symbols:
        try:
            expirations = ticker_obj.options
            if not expirations:
                continue

            for expiry in expirations[:3]:
                opt_chain = ticker_obj.option_chain(expiry)
                puts = opt_chain.puts.copy()
                if puts.empty:
                    continue

                expiry_date = pd.to_datetime(expiry)
                today = pd.Timestamp.now().normalize()
                dte = max((expiry_date - today).days, 1)

                otm_puts = puts[puts["strike"] < current_price].copy()

                # 保证金限制
                otm_puts["预估保证金"] = otm_puts["strike"] * 100
                otm_puts = otm_puts[otm_puts["预估保证金"] <= max_budget]

                # 活跃度过滤
                otm_puts["volume"] = otm_puts["volume"].fillna(0)
                otm_puts["openInterest"] = otm_puts["openInterest"].fillna(0)
                otm_puts = otm_puts[
                    (otm_puts["volume"] >= min_volume)
                    & (otm_puts["openInterest"] >= min_open_interest)
                ]

                if otm_puts.empty:
                    continue

                # 价差风控 (<= 35%)
                otm_puts["bid_ask_spread"] = otm_puts["ask"] - otm_puts["bid"]
                otm_puts["spread_ratio"] = np.where(
                    otm_puts["ask"] > 0,
                    otm_puts["bid_ask_spread"] / otm_puts["ask"],
                    1.0,
                )
                otm_puts = otm_puts[otm_puts["spread_ratio"] <= 0.35]

                # 价格评估
                otm_puts["权利金(Mid)"] = (
                    otm_puts["bid"] + otm_puts["ask"]
                ) / 2
                otm_puts = otm_puts[otm_puts["权利金(Mid)"] > 0]

                if otm_puts.empty:
                    continue

                # 智能推荐挂单价
                otm_puts["推荐挂单价"] = np.maximum(
                    otm_puts["bid"], otm_puts["权利金(Mid)"] - 0.01
                )

                otm_puts["股票代码"] = symbol
                otm_puts["到期日"] = expiry
                otm_puts["DTE"] = dte
                otm_puts["股价"] = current_price

                otm_puts["安全边际(%)"] = (
                    (current_price - otm_puts["strike"]) / current_price
                ) * 100
                otm_puts["年化收益率(%)"] = (
                    (otm_puts["权利金(Mid)"] / otm_puts["strike"])
                    * (365 / dte)
                    * 100
                )

                # 行权概率 (Delta)
                if (
                    "delta" in otm_puts.columns
                    and not otm_puts["delta"].isna().all()
                ):
                    otm_puts["行权概率(%)"] = otm_puts["delta"].abs() * 100
                else:
                    otm_puts["行权概率(%)"] = np.maximum(
                        1.0, 50.0 - otm_puts["安全边际(%)"] * 2.2
                    )

                otm_puts = otm_puts[
                    (otm_puts["行权概率(%)"] <= 35.0)
                    & (otm_puts["安全边际(%)"] >= 2.0)
                ]

                if otm_puts.empty:
                    continue

                # 财报风险监测
                otm_puts["财报预警"] = check_earnings_warning(
                    ticker_obj, expiry
                )

                otm_puts["评估值"] = (
                    otm_puts["年化收益率(%)"]
                    * otm_puts["安全边际(%)"]
                    / (otm_puts["行权概率(%)"] + 1)
                )
                all_opportunities.append(otm_puts)

        except Exception:
            continue

    if not all_opportunities:
        st.error(
            "😭 在当前风控与预算条件下未扫出期权，建议适当微调侧边栏参数。"
        )
        return

    result_df = pd.concat(all_opportunities, ignore_index=True)
    result_df = result_df.sort_values(by="评估值", ascending=False).head(15)

    st.session_state["scan_results"] = result_df

    # ------------------ 2.0 可视化图表 ------------------
    st.subheader("📊 收益 vs 风险 离散分布图")
    fig = px.scatter(
        result_df,
        x="安全边际(%)",
        y="年化收益率(%)",
        size="volume",
        color="行权概率(%)",
        hover_name="股票代码",
        hover_data={
            "strike": ":$.2f",
            "权利金(Mid)": ":$.2f",
            "推荐挂单价": ":$.2f",
            "到期日": True,
            "财报预警": True,
        },
        labels={
            "安全边际(%)": "安全边际 (%) [越靠右越安全]",
            "年化收益率(%)": "年化收益率 (%) [越靠上收益越高]",
        },
        color_continuous_scale="RdYlGn_r",
    )
    st.plotly_chart(fig, use_container_width=True)

    # ------------------ 3.0 组合现金流计算器 ------------------
    st.markdown("---")
    st.subheader("💰 拟合组合现金流计算器")
    st.caption("在下方多选您看中的标的，系统将自动汇总您的资金占用与即时现金收入：")

    options_list = [
        f"{row['股票代码']} | {row['到期日']} | 行权价:${row['strike']:.2f} | 预估单手保证金:${row['预估保证金']:,.0f} | 权利金:${row['权利金(Mid)'] * 100:.0f}"
        for _, row in result_df.iterrows()
    ]

    selected_opts = st.multiselect(
        "选择准备同时操作的 Sell Put 组合：", options_list
    )

    if selected_opts:
        total_margin = 0
        total_cash = 0
        selected_codes = []

        for opt in selected_opts:
            code = opt.split(" | ")[0]
            expiry = opt.split(" | ")[1]
            matched = result_df[
                (result_df["股票代码"] == code)
                & (result_df["到期日"] == expiry)
            ].iloc[0]

            total_margin += matched["预估保证金"]
            total_cash += matched["权利金(Mid)"] * 100
            selected_codes.append(f"{code} (${matched['strike']})")

        c1, c2, c3 = st.columns(3)
        c1.metric("🔒 选中组合需要资金 (总保证金)", f"${total_margin:,.0f}")
        c2.metric(
            "💵 即刻落袋权利金 (现金流)",
            f"${total_cash:,.2f}",
            delta=f"整体回报率 {(total_cash / total_margin) * 100:.2f}%",
        )
        c3.metric("🎯 包含标的数量", f"{len(selected_codes)} 只标的")

    st.markdown("---")

    # ------------------ 数据列表 ------------------
    st.subheader("📋 详细数据与实盘挂单指南")
    display_df = pd.DataFrame(
        {
            "代码": result_df["股票代码"],
            "现价": result_df["股价"].map("${:.2f}".format),
            "财报预警": result_df["财报预警"],
            "到期日": result_df["到期日"],
            "DTE": result_df["DTE"],
            "行权价": result_df["strike"].map("${:.2f}".format),
            "权利金(Mid)": result_df["权利金(Mid)"].map("${:.2f}".format),
            "🎯推荐挂单价": result_df["推荐挂单价"].map("${:.2f}".format),
            "价差比": result_df["spread_ratio"].map("{:.1%}".format),
            "需保证金": result_df["预估保证金"].map("${:,.0f}".format),
            "安全边际": result_df["安全边际(%)"].map("{:.2f}%".format),
            "年化收益率": result_df["年化收益率(%)"].map(
                "{:.2f}%".format
            ),
            "行权概率": result_df["行权概率(%)"].map("{:.2f}%".format),
            "成交量": result_df["volume"].astype(int),
            "持仓量": result_df["openInterest"].astype(int),
            "综合评分": result_df["评估值"].map("{:.2f}".format),
        }
    )

    st.dataframe(display_df, use_container_width=True, hide_index=True)


if btn_scan:
    scan_options_v3()
