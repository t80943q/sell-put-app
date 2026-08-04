import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Sell Put 智能量化终端", layout="wide")
st.title("🚀 Sell Put 智能量化终端 (实盘真流动性版)")

# ================= 侧边栏风控设置 =================
st.sidebar.header("⚙️ 筛选风控与预算")
min_price = st.sidebar.number_input("最低股价 ($)", value=2.0, step=1.0)
max_price = st.sidebar.number_input("最高股价 ($)", value=250.0, step=10.0)
max_budget = st.sidebar.number_input("单笔预算上限 ($)", value=3000, step=500)
min_volume = st.sidebar.number_input("最低成交量", value=0, step=1)
min_open_interest = st.sidebar.number_input(
    "最低持仓量(张)", value=20, step=5
)  # 默认设为20张，斩断1张僵尸单

btn_scan = st.sidebar.button("🚀 启动全能量化扫盘", type="primary")

target_pool = [
    "XPEV",
    "BABA",
    "JD",
    "NVDA",
    "TSLA",
    "AAPL",
    "SOXL",
    "TQQQ",
    "IWM",
    "LCID",
    "SOFI",
    "NIO",
    "F",
    "HOOD",
    "PLTR",
    "CLSK",
    "MARA",
    "RIOT",
    "GRAB",
    "AFRM",
    "SNAP",
    "DKNG",
    "COIN",
    "CCL",
    "RIVN",
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


def fetch_all_data_fast(
    min_price_val,
    max_price_val,
    max_budget_val,
    min_vol_val,
    min_oi_val,
):
  all_opportunities = []

  progress_bar = st.progress(0)
  status_text = st.empty()

  for idx, sym in enumerate(target_pool):
    progress_bar.progress((idx + 1) / len(target_pool))
    status_text.text(f"🔍 正在扫盘标的: {sym} ({idx+1}/{len(target_pool)})")

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
        if otm_puts.empty:
          continue

        otm_puts["预估保证金"] = otm_puts["strike"] * 100
        otm_puts = otm_puts[otm_puts["预估保证金"] <= max_budget_val]

        if otm_puts.empty:
          continue

        # 🌟【死单抹杀规则 1】：必须有真实未平仓持仓量，过滤未平仓数仅个位数的无人问津死单
        otm_puts["openInterest"] = otm_puts["openInterest"].fillna(0)
        otm_puts["volume"] = otm_puts["volume"].fillna(0)

        # 只要未平仓小于设定门槛（默认20张），直接抹杀
        otm_puts = otm_puts[otm_puts["openInterest"] >= max(min_oi_val, 15)]

        if otm_puts.empty:
          continue

        otm_puts["bid"] = otm_puts["bid"].fillna(0.0)
        otm_puts["ask"] = otm_puts["ask"].fillna(0.0)
        otm_puts["lastPrice"] = otm_puts["lastPrice"].fillna(0.0)

        # 挂单参考价
        otm_puts["Mid"] = (otm_puts["bid"] + otm_puts["ask"]) / 2
        otm_puts["推荐挂单价"] = np.where(
            otm_puts["bid"] > 0,
            np.minimum(otm_puts["Mid"], otm_puts["bid"] * 1.15),
            otm_puts["lastPrice"],
        )

        # 🌟【死单抹杀规则 2】：极度深虚值（权利金单手低于 $2 美元/0.02）坚决剔除
        otm_puts = otm_puts[otm_puts["推荐挂单价"] >= 0.02]

        if otm_puts.empty:
          continue

        otm_puts["股票代码"] = sym
        otm_puts["到期日"] = expiry
        otm_puts["DTE"] = dte
        otm_puts["股价"] = current_price

        otm_puts["安全边际(%)"] = (
            (current_price - otm_puts["strike"]) / current_price
        ) * 100

        # 🌟【死单抹杀规则 3】：安全边际超过 38% 的过远虚值单（如 SNAP $2.5P）无真实买家，剔除
        otm_puts = otm_puts[otm_puts["安全边际(%)"] <= 38.0]

        if otm_puts.empty:
          continue

        otm_puts["年化收益率(%)"] = (
            (otm_puts["推荐挂单价"] / otm_puts["strike"])
            * (365 / dte)
            * 100
        )

        # Delta/行权概率校验
        if "delta" in otm_puts.columns and not otm_puts["delta"].isna().all():
          otm_puts["行权概率(%)"] = otm_puts["delta"].abs() * 100
        else:
          otm_puts["行权概率(%)"] = np.maximum(
              1.0, 50.0 - otm_puts["安全边际(%)"] * 2.2
          )

        # 🌟【死单抹杀规则 4】：行权概率（Delta）过小（< 2.0%）说明做市商已放弃报价，剔除
        otm_puts = otm_puts[otm_puts["行权概率(%)"] >= 2.0]

        if otm_puts.empty:
          continue

        otm_puts["财报预警"] = check_earnings_warning(t, expiry)

        # 真实合理评分公式
        otm_puts["评估值"] = (
            otm_puts["年化收益率(%)"]
            * otm_puts["安全边际(%)"]
            / (otm_puts["行权概率(%)"] + 1)
        )

        all_opportunities.append(otm_puts)

    except Exception:
      continue

  progress_bar.empty()
  status_text.empty()

  if not all_opportunities:
    return None

  result_df = pd.concat(all_opportunities, ignore_index=True)
  return result_df.sort_values(by="评估值", ascending=False).head(15)


# ------------------ 页面控制与渲染 ------------------
if btn_scan:
  res = fetch_all_data_fast(
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
  st.warning(
      "😭 未扫出兼具真实流动性与高性价比的期权，建议适当微调侧边栏预算。"
  )

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
    label = f"[{row['股票代码']}] {row['到期日']} | 行权价:${row['strike']:.2f} | 保证金:${row['预估保证金']:,.0f} | 真实推荐权利金:${row['推荐挂单价'] * 100:.0f}"
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
      selected_codes.append(f"{matched['股票代码']} (${matched['strike']})")

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
  display_df = pd.DataFrame({
      "代码": result_df["股票代码"],
      "现价": result_df["股价"].map("${:.2f}".format),
      "财报预警": result_df["财报预警"],
      "到期日": result_df["到期日"],
      "DTE": result_df["DTE"],
      "行权价": result_df["strike"].map("${:.2f}".format),
      "🎯推荐实盘挂单价": result_df["推荐挂单价"].map("${:.2f}".format),
      "需保证金": result_df["预估保证金"].map("${:,.0f}".format),
      "安全边际": result_df["安全边际(%)"].map("{:.2f}%".format),
      "年化收益率": result_df["年化收益率(%)"].map("{:.2f}%".format),
      "行权概率": result_df["行权概率(%)"].map("{:.2f}%".format),
      "成交量": result_df["volume"].astype(int),
      "持仓量": result_df["openInterest"].astype(int),
      "综合评分": result_df["评估值"].map("{:.2f}".format),
  })

  st.dataframe(display_df, use_container_width=True, hide_index=True)
