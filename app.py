import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="Sell Put 智能量化终端", layout="wide"
)
st.title("🚀 Sell Put 智能量化终端 (真实版)")

# ================= 侧边栏风控设置 =================
st.sidebar.header("⚙️ 筛选风控与预算")
min_price = st.sidebar.number_input("最低股价 ($)", value=2.0, step=1.0)
max_price = st.sidebar.number_input("最高股价 ($)", value=100.0, step=5.0)
max_budget = st.sidebar.number_input("单笔预算上限 ($)", value=5000, step=500)
min_volume = st.sidebar.number_input("最低成交量", value=0, step=1)
min_open_interest = st.sidebar.number_input("最低持仓量", value=0, step=1)

btn_scan = st.sidebar.button("🚀 启动全能量化扫盘", type="primary")

target_pool = [
    "LCID",
    "SOFI",
    "NIO",
    "F",
    "RIVN",
    "PLTR",
    "HOOD",
    "CLSK",
    "MARA",
    "RIOT",
    "GRAB",
    "AFRM",
    "SNAP",
    "DKNG",
    "AAL",
    "CCL",
    "PFE",
    "BAC",
    "INTC",
    "SOXL",
    "TQQQ",
    "LABU",
    "AMD",
    "NVDA",
    "TSLA",
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "QQQ",
    "SPY",
    "IWM",
    "TLT",
    "SLV",
]


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


@st.cache_data(ttl=600, show_spinner=False)
def fetch_all_data(
    min_price_val,
    max_price_val,
    max_budget_val,
    min_vol_val,
    min_oi_val,
):
    all_opportunities = []

    for sym in target_pool:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="1d")
            if hist.empty:
                continue

            current_price = hist["Close"].iloc[-1]
            if not (min_price_val <= current_price <= max_price_val):
                continue

            expirations = t.options
            if not expirations:
                continue

            for expiry in expirations[:3]:
                opt_chain = t.option_chain(expiry)
                puts = opt_chain.puts.copy()
                if puts.empty:
                    continue

                expiry_date = pd.to_datetime(expiry)
                today = pd.Timestamp.now().normalize()
                dte = max((expiry_date - today).days, 1)

                otm_puts = puts[puts["strike"] < current_price].copy()

                # 保证金限制
                otm_puts["预估保证金"] = otm_puts["strike"] * 100
                otm_puts = otm_puts[otm_puts["预估保证金"] <= max_budget_val]

                if otm_puts.empty:
                    continue

                # 🌟【防假报价关键修复 1】：做市商出价必须大于 0 (Bid > 0)，剔除无人买入的极度离谱死单
                otm_puts = otm_puts[otm_puts["bid"] > 0.01]

                if otm_puts.empty:
                    continue

                # 🌟【防假报价关键修复 2】：买卖价差不能比 Bid 夸张太多 (Bid/Ask 比例过滤)
                otm_puts["bid_ask_spread"] = otm_puts["ask"] - otm_puts["bid"]
                # 买卖价格不能脱节超过 3 倍
                otm_puts = otm_puts[otm_puts["ask"] <= otm_puts["bid"] * 3.0]

                if otm_puts.empty:
                    continue

                # 活跃度过滤
                otm_puts["volume"] = otm_puts["volume"].fillna(0)
                otm_puts["openInterest"] = otm_puts["openInterest"].fillna(0)
                otm_puts = otm_puts[
                    (otm_puts["volume"] >= min_vol_val)
                    & (otm_puts["openInterest"] >= min_oi_val)
                ]

                if otm_puts.empty:
                    continue

                # 实盘可成交的真实权利金计算 (优先用 Bid~Mid 之间)
                otm_puts["权利金(Mid)"] = (
                    otm_puts["bid"] + otm_puts["ask"]
                ) / 2
                
                # 推荐挂单价：以真实 Bid 买价为基准，避免挂高买家不接单
                otm_puts["推荐挂单价"] = np.minimum(
                    otm_puts["权利金(Mid)"], otm_puts["bid"] * 1.15
                )

                otm_puts["股票代码"] = sym
                otm_puts["到期日"] = expiry
                otm_puts["DTE"] = dte
                otm_puts["股价"] = current_price

                otm_puts["安全边际(%)"] = (
                    (current_price - otm_puts["strike"]) / current_price
                ) * 100
                otm_puts["年化收益率(%)"] = (
                    (otm_puts["推荐挂单价"] / otm_puts["strike"])
                    * (365 / dte)
                    * 100
                )

                if (
                    "delta" in otm_puts.columns
                    and not otm_puts["delta"].isna().all()
                ):
                    otm_puts["行权概率(%)"] = otm_puts["delta"].abs() * 100
                else:
                    otm_puts["行权概率(%)"] = np.maximum(
                        1.0, 50.0 - otm_puts["安全边际(%)"] * 2.2
                    )

                # 风控限制：行权概率 <= 40%，安全边际 >= 1%
                otm_puts = otm_puts[
                    (otm_puts["行权概率(%)"] <= 40.0)
                    & (otm_puts["安全边际(%)"] >= 1.0)
                ]

                if otm_puts.empty:
                    continue

                otm_puts["财报预警"] = check_earnings_warning(t, expiry)

                # 评分剔除异常年化（年化超过 200% 的节点大概率有流动性陷阱，打折评估）
                adjusted_yield = np.where(
                    otm_puts["年化收益率(%)"] > 200.0,
                    200.0,
                    otm_puts["年化收益率(%)"],
                )
                otm_puts["评估值"] = (
                    adjusted_yield
                    * otm_puts["安全边际(%)"]
                    / (otm_puts["行权概率(%)"] + 1)
                )

                all_opportunities.append(otm_puts)

        except Exception:
            continue

    if not all_opportunities:
        return None

    result_df = pd.concat(all_opportunities, ignore_index=True)
    return result_df.sort_values(by="评估值", ascending=False).head(15)


# ------------------ 页面控制与渲染 ------------------
if btn_scan:
    with st.spinner("🤖 正在为您过滤假报价死单，精准扫描实盘可成交标的..."):
        res = fetch_all_data(
            min_price, max_price, max_budget, min_volume, min_open_interest
        )
        if res is None or res.empty:
            st.session_state["scan_error"] = True
            st.session_state["scan_results"] = None
        else:
            st.session_state["scan_error"] = False
            st.session_state["scan_results"] = res

if st.session_state.get("scan_error", False) and (
    "scan_results" not in st.session_state or st.session_state["scan_results"] is None
):
    st.warning("😭 暂时未扫出可交易期权，请调宽左侧【最高股价】或【单笔预算上限】。")

if (
    "scan_results" in st.session_state
    and st.session_state["scan_results"] is not None
):
    result_df = st.session_state["scan_results"]

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

    st.markdown("---")
    st.subheader("💰 拟合组合现金流计算器")
    st.caption(
        "在下方多选您看中的标的，系统将自动汇总您的资金占用与即时现金收入："
    )

    options_map = {}
    options_list = []
    for idx, row in result_df.iterrows():
        label = f"[{row['股票代码']}] {row['到期日']} | 行权价:${row['strike']:.2f} | 保证金:${row['预估保证金']:,.0f} | 预估权利金:${row['推荐挂单价'] * 100:.0f}"
        options_list.append(label)
        options_map[label] = row

    selected_opts = st.multiselect(
        "选择准备同时操作的 Sell Put 组合：", options_list
    )

    if selected_opts:
        total_margin = 0
        total_cash = 0
        selected_codes = []

        for label in selected_opts:
            matched = options_map[label]
            total_margin += matched["预估保证金"]
            total_cash += matched["推荐挂单价"] * 100
            selected_codes.append(
                f"{matched['股票代码']} (${matched['strike']})"
            )

        c1, c2, c3 = st.columns(3)
        c1.metric("🔒 选中组合需要资金 (总保证金)", f"${total_margin:,.0f}")
        c2.metric(
            "💵 即刻落袋权利金 (现金流)",
            f"${total_cash:,.2f}",
            delta=(
                f"整体回报率 {(total_cash / total_margin) * 100:.2f}%"
                if total_margin > 0
                else "0%"
            ),
        )
        c3.metric("🎯 包含标的数量", f"{len(selected_codes)} 只标的")

    st.markdown("---")

    st.subheader("📋 详细数据与实盘挂单指南")
    display_df = pd.DataFrame(
        {
            "代码": result_df["股票代码"],
            "现价": result_df["股价"].map("${:.2f}".format),
            "财报预警": result_df["财报预警"],
            "到期日": result_df["到期日"],
            "DTE": result_df["DTE"],
            "行权价": result_df["strike"].map("${:.2f}".format),
            "🎯推荐挂单价": result_df["推荐挂单价"].map("${:.2f}".format),
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
