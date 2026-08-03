import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Sell Put 量化分析器", layout="wide")
st.title("🚀 Sell Put 智能量化扫盘面板")

# 侧边栏控件设置
st.sidebar.header("⚙️ 筛选风控设置")
min_price = st.sidebar.number_input("最低股价 ($)", value=5.0, step=1.0)
max_price = st.sidebar.number_input("最高股价 ($)", value=15.0, step=1.0)
max_budget = st.sidebar.number_input("总预算上限 ($)", value=1500, step=100)
min_volume = st.sidebar.number_input("最低成交量", value=10, step=5)
min_open_interest = st.sidebar.number_input("最低持仓量", value=50, step=10)

btn_scan = st.sidebar.button("🚀 启动量化风控扫盘", type="primary")

# 预设基础热门股票池
target_pool = [
    "SOFI",
    "F",
    "NIO",
    "RIVN",
    "HOOD",
    "PLTR",
    "AMC",
    "AAL",
    "MARA",
    "LCID",
    "INTC",
    "BAC",
    "CCL",
    "PFE",
    "CLOV",
    "GRAB",
    "AUPH",
    "KGC",
    "VALE",
    "RIG",
    "BMY",
    "SNAP",
    "DKNG",
    "WFC",
    "PBR",
]


def scan_options():
    st.info("🤖 正在为您全网扫描活跃标的期权链...")
    final_symbols = []

    # 股价区间过滤
    for sym in target_pool:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="1d")
            if not hist.empty:
                p = hist["Close"].iloc[-1]
                if min_price <= p <= max_price:
                    final_symbols.append((sym, p))
        except:
            continue

    if not final_symbols:
        st.warning(
            "未找到符合股价区间的股票，请调宽【最低/最高股价】区间。"
        )
        return

    all_opportunities = []

    for symbol, current_price in final_symbols:
        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            if not expirations:
                continue

            for expiry in expirations[:3]:
                opt_chain = ticker.option_chain(expiry)
                puts = opt_chain.puts.copy()
                if puts.empty:
                    continue

                expiry_date = pd.to_datetime(expiry)
                today = pd.Timestamp.now().normalize()
                dte = max((expiry_date - today).days, 1)

                otm_puts = puts[puts["strike"] < current_price].copy()
                otm_puts["预估保证金"] = otm_puts["strike"] * 100
                otm_puts = otm_puts[otm_puts["预估保证金"] <= max_budget]

                otm_puts["volume"] = otm_puts["volume"].fillna(0)
                otm_puts["openInterest"] = otm_puts["openInterest"].fillna(0)
                otm_puts = otm_puts[
                    (otm_puts["volume"] >= min_volume)
                    & (otm_puts["openInterest"] >= min_open_interest)
                ]

                if otm_puts.empty:
                    continue

                otm_puts["bid_ask_spread"] = otm_puts["ask"] - otm_puts["bid"]
                otm_puts["spread_ratio"] = np.where(
                    otm_puts["ask"] > 0,
                    otm_puts["bid_ask_spread"] / otm_puts["ask"],
                    1.0,
                )
                otm_puts = otm_puts[otm_puts["spread_ratio"] <= 0.30]

                otm_puts["权利金(Mid)"] = (
                    otm_puts["bid"] + otm_puts["ask"]
                ) / 2
                otm_puts = otm_puts[otm_puts["权利金(Mid)"] > 0]

                if otm_puts.empty:
                    continue

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
                    (otm_puts["行权概率(%)"] <= 30.0)
                    & (otm_puts["安全边际(%)"] >= 3.0)
                ]

                if otm_puts.empty:
                    continue

                otm_puts["评估值"] = (
                    otm_puts["年化收益率(%)"]
                    * otm_puts["安全边际(%)"]
                    / (otm_puts["行权概率(%)"] + 1)
                )
                all_opportunities.append(otm_puts)

        except:
            continue

    if not all_opportunities:
        st.error("未筛选到同时满足风控条件与资金上限的期权，请微调条件。")
        return

    result_df = pd.concat(all_opportunities, ignore_index=True)
    result_df = result_df.sort_values(by="评估值", ascending=False).head(
        15
    )

    display_df = pd.DataFrame(
        {
            "代码": result_df["股票代码"],
            "现价": result_df["股价"].map("${:.2f}".format),
            "到期日": result_df["到期日"],
            "DTE": result_df["DTE"],
            "行权价": result_df["strike"].map("${:.2f}".format),
            "权利金(Mid)": result_df["权利金(Mid)"].map("${:.2f}".format),
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

    st.success("✅ 扫盘完成！为您呈现综合性价比最高的 Top 15 方案：")
    st.dataframe(display_df, use_container_width=True)


if btn_scan:
    scan_options()
