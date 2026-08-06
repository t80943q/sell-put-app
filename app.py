import time
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Sell Put 4.0 机构级智能终端", layout="wide")
st.title("🚀 Sell Put 智能量化终端 4.0 (老股民实盘臻选·黄金平衡版)")


# ---------------- 🎯 生成 Moomoo 搜索框专属期权代码 ----------------
def generate_moomoo_search_code(symbol, expiry, strike):
    clean_date = str(expiry).replace("-", "")[2:]
    strike_int = int(round(float(strike) * 1000))
    return f"{symbol}{clean_date}P{strike_int}"


# ================= 侧边栏风控设置 =================
st.sidebar.header("⚙️ 筛选风控与策略模式")

max_budget = st.sidebar.number_input("💵 单笔预算上限 ($)", value=3000, step=500, help="系统根据预算自动匹配最佳行权价与股价")

allowed_max_strike = max_budget / 100.0
st.sidebar.info(f"🎯 预算 ${max_budget:,.0f} 对应：\n* 最大行权价：${allowed_max_strike:.2f}\n* 动态匹配股价：$2.0 ~ ${allowed_max_strike * 1.35:.1f}")

st.sidebar.subheader("🌊 盘口与买方对手盘硬风控")
min_volume = st.sidebar.number_input("最低成交量(张)", value=0, step=5)
min_open_interest = st.sidebar.number_input("最低持仓量(张)", value=0, step=5)
min_bid_price = st.sidebar.number_input(
    "最低买一出价 (Bid $)", value=0.02, step=0.01, help="买方必须出价至少此金额，彻底干掉Bid=0的做市商死水单"
)

filter_earnings = st.sidebar.checkbox("🛡️ 开启财报避险 (隐藏跨财报期权)", value=False)
btn_scan = st.sidebar.button("🚀 启动 4.0 全能量化扫盘", type="primary")

target_pool = [
    "XPEV", "BABA", "JD", "NVDA", "TSLA", "AAPL", "SOXL", "TQQQ", "IWM",
    "LCID", "SOFI", "NIO", "F", "HOOD", "PLTR", "CLSK", "MARA", "RIOT",
    "GRAB", "AFRM", "SNAP", "DKNG", "COIN", "CCL", "RIVN"
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
                return False, "✅ 安全(无预警)"

            expiry_dt = pd.to_datetime(expiry_str).tz_localize(None)
            today = pd.Timestamp.now().normalize()

            for ed in earnings_dates:
                ed_dt = pd.to_datetime(ed).tz_localize(None)
                if today <= ed_dt <= expiry_dt:
                    return True, f"⚠️ 财报日({ed_dt.strftime('%m-%d')})"
    except Exception:
        pass
    return False, "✅ 安全(无预警)"


def get_iv_rank_status(implied_vol):
    iv_val = implied_vol * 100 if implied_vol else 0
    if iv_val >= 70:
        return f"🔥 极高({iv_val:.0f}%)"
    elif iv_val >= 45:
        return f"⚡ 中高({iv_val:.0f}%)"
    elif iv_val >= 25:
        return f"⚖️ 适中({iv_val:.0f}%)"
    else:
        return f"🧊 偏低({iv_val:.0f}%)"


@st.cache_data(ttl=600, show_spinner=False)
def fetch_all_data_v4(
    max_budget_val,
    min_vol_val,
    min_oi_val,
    min_bid_val,
    avoid_earnings,
):
    all_opportunities = []
    allowed_max_strike = max_budget_val / 100.0
    allowed_max_stock_price = allowed_max_strike * 1.35

    for idx, sym in enumerate(target_pool):
        try:
            time.sleep(0.05)
            t = yf.Ticker(sym)
            hist = t.history(period="1d")
            
            if hist.empty:
                continue

            current_price = hist["Close"].iloc[-1]
            if current_price > allowed_max_stock_price:
                continue

            expirations = t.options
            if not expirations:
                continue

            for expiry in expirations[:3]:
                has_earnings, earnings_label = check_earnings_warning(t, expiry)
                if avoid_earnings and has_earnings:
                    continue

                opt_chain = t.option_chain(expiry)
                puts = opt_chain.puts.copy()
                if puts.empty:
                    continue

                expiry_date = pd.to_datetime(expiry)
                today = pd.Timestamp.now().normalize()
                dte = max((expiry_date - today).days, 1)

                otm_puts = puts[puts["strike"] < current_price].copy()
                if otm_puts.empty:
                    continue

                otm_puts["预估保证金"] = otm_puts["strike"] * 100
                otm_puts = otm_puts[otm_puts["预估保证金"] <= max_budget_val]
                if otm_puts.empty:
                    continue

                # 基础数据安全填充
                otm_puts["openInterest"] = otm_puts["openInterest"].fillna(0)
                otm_puts["volume"] = otm_puts["volume"].fillna(0)
                otm_puts["bid"] = otm_puts["bid"].fillna(0.0)
                otm_puts["ask"] = otm_puts["ask"].fillna(0.0)
                otm_puts["impliedVolatility"] = otm_puts["impliedVolatility"].fillna(0.0)

                # ---------------- 🚨 老股民风控：淘汰没有真实买买盘或买卖价差极度畸形的离谱单 ----------------
                otm_puts = otm_puts[otm_puts["bid"] >= min_bid_val]
                if otm_puts.empty:
                    continue

                # 剔除买卖价差 (Ask - Bid) 超过 Bid 本身 1.5 倍的死水离散单
                otm_puts["Spread"] = otm_puts["ask"] - otm_puts["bid"]
                otm_puts = otm_puts[(otm_puts["ask"] == 0) | (otm_puts["Spread"] <= np.maximum(0.15, otm_puts["bid"] * 1.2))]

                if min_oi_val > 0:
                    otm_puts = otm_puts[otm_puts["openInterest"] >= min_oi_val]
                if min_vol_val > 0:
                    otm_puts = otm_puts[otm_puts["volume"] >= min_vol_val]

                if otm_puts.empty:
                    continue

                # 🎯 老股民黄金博弈算法：推荐价 = Bid + 向上多博弈 $0.01~$0.02 (买一价的1.08倍，但绝不超 Mid)
                otm_puts["Mid"] = np.where(
                    (otm_puts["bid"] > 0) & (otm_puts["ask"] > 0),
                    (otm_puts["bid"] + otm_puts["ask"]) / 2,
                    otm_puts["bid"] + 0.02
                )
                otm_puts["推荐挂单价"] = np.minimum(otm_puts["Mid"], np.maximum(otm_puts["bid"] + 0.01, otm_puts["bid"] * 1.08))

                otm_puts["股票代码"] = sym
                otm_puts["到期日"] = expiry
                otm_puts["DTE"] = dte
                otm_puts["股价"] = current_price

                otm_puts["Moomoo代码"] = otm_puts.apply(
                    lambda r: generate_moomoo_search_code(r["股票代码"], r["到期日"], r["strike"]), axis=1
                )

                otm_puts["安全边际(%)"] = ((current_price - otm_puts["strike"]) / current_price) * 100
                otm_puts = otm_puts[otm_puts["安全边际(%)"] <= 40.0]
                if otm_puts.empty:
                    continue

                otm_puts["年化收益率(%)"] = (otm_puts["推荐挂单价"] / otm_puts["strike"]) * (365 / dte) * 100

                if "delta" in otm_puts.columns and not otm_puts["delta"].isna().all():
                    otm_puts["行权概率(%)"] = otm_puts["delta"].abs() * 100
                else:
                    otm_puts["行权概率(%)"] = np.maximum(0.5, 50.0 - otm_puts["安全边际(%)"] * 2.2)

                otm_puts["行权概率(%)"] = otm_puts["行权概率(%)"].fillna(15.0)

                otm_puts["财报预警"] = earnings_label
                otm_puts["IV状态"] = otm_puts["impliedVolatility"].apply(get_iv_rank_status)
                otm_puts["评估值"] = (otm_puts["年化收益率(%)"] * otm_puts["安全边际(%)"]) / (otm_puts["行权概率(%)"] + 1)

                all_opportunities.append(otm_puts)

        except Exception:
            continue

    if not all_opportunities:
        return None

    result_df = pd.concat(all_opportunities, ignore_index=True)
    return result_df.sort_values(by="评估值", ascending=False).head(15)


# ------------------ 页面控制与渲染 ------------------
if btn_scan:
    with st.spinner("🤖 正在为您匹配真实有买盘的活跃期权..."):
        res = fetch_all_data_v4(
            max_budget,
            min_volume,
            min_open_interest,
            min_bid_price,
            filter_earnings,
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
    st.warning("😭 未找到符合要求的标的，建议将侧边栏【单笔预算上限】调至 $5,000 再次尝试。")

if "scan_results" in st.session_state and st.session_state["scan_results"] is not None:
    result_df = st.session_state["scan_results"]

    st.subheader("📊 收益 vs 风险 离散分布图 (气泡大小表示成交量)")
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
            "bid": ":$.2f",
            "到期日": True,
            "IV状态": True,
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

    options_map = {}
    options_list = []
    for idx, row in result_df.iterrows():
        label = f"[{row['股票代码']}] 到期日:{row['到期日']}(还剩{row['DTE']}天) | 行权价:${row['strike']:.2f} | 保证金:${row['预估保证金']:,.0f} | 推荐权利金:${row['推荐挂单价'] * 100:.0f}"
        options_list.append(label)
        options_map[label] = row

    selected_opts = st.multiselect("选择准备同时操作的 Sell Put 组合：", options_list)

    if selected_opts:
        total_margin = sum(options_map[l]["预估保证金"] for l in selected_opts)
        total_cash = sum(options_map[l]["推荐挂单价"] * 100 for l in selected_opts)

        c1, c2, c3 = st.columns(3)
        c1.metric("🔒 选中组合需要资金 (总保证金)", f"${total_margin:,.0f}")
        c2.metric(
            "💵 即刻落袋权利金 (现金流)",
            f"${total_cash:,.2f}",
            delta=f"整体回报率 {(total_cash / total_margin) * 100:.2f}%" if total_margin > 0 else "0%",
        )
        c3.metric("🎯 包含标的数量", f"{len(selected_opts)} 只标的")

    st.markdown("---")

    # ---------------- 📋 16 项完整核心数据列 ----------------
    st.subheader("📋 详细数据与实盘挂单指南")
    display_df = pd.DataFrame({
        "代码": result_df["股票代码"],
        "现价": result_df["股价"].map("${:.2f}".format),
        "IV环境": result_df["IV状态"],
        "财报预警": result_df["财报预警"],
        "到期日": result_df["到期日"],
        "DTE": result_df["DTE"].map("{} 天".format),
        "行权价": result_df["strike"].map("${:.2f}".format),
        "🎯推荐限价": result_df["推荐挂单价"].map("${:.2f}".format),
        "买一价(Bid)": result_df["bid"].map("${:.2f}".format),
        "需保证金": result_df["预估保证金"].map("${:,.0f}".format),
        "安全边际": result_df["安全边际(%)"].map("{:.2f}%".format),
        "🚀预计年化": result_df["年化收益率(%)"].map("{:.2f}%".format),
        "🎲行权概率": result_df["行权概率(%)"].map("{:.2f}%".format),
        "成交量": result_df["volume"].astype(int),
        "持仓量": result_df["openInterest"].astype(int),
        "综合评分": result_df["评估值"].map("{:.2f}".format),
    })

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("⚡ Moomoo Top 5 最佳组合快捷挂单指南")

    moomoo_search_codes = []
    order_lines = []
    for idx, row in result_df.head(5).iterrows():
        moomoo_search_codes.append(row["Moomoo代码"])
        order_line = f"Moomoo代码: {row['Moomoo代码']} | SELL PUT 1张 | 🎯推荐限价:${row['推荐挂单价']:.2f} (Bid:${row['bid']:.2f}) | 预计年化:{row['年化收益率(%)']:.1f}% | 预估收入:${row['推荐挂单价']*100:.0f}"
        order_lines.append(order_line)

    st.markdown("#### 1️⃣ Moomoo App 搜索代码列表（快捷复制直接在 Moomoo 顶部粘贴搜索）：")
    st.code("\n".join(moomoo_search_codes), language="text")

    st.markdown("#### 2️⃣ 完整挂单指导详情：")
    st.code("\n".join(order_lines), language="text")
