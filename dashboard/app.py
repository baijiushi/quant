"""
dashboard/app.py
量化选股 · B1策略看盘界面（Streamlit）

启动方式：
    streamlit run dashboard/app.py
    streamlit run dashboard/app.py -- --config config/dashboard.yaml
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml
from plotly.subplots import make_subplots

# 确保项目根目录在路径中
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "pipeline"))

# =============================================================================
# 配置加载
# =============================================================================

_DEFAULT_CONFIG = _ROOT / "config" / "dashboard.yaml"


@st.cache_data(ttl=60)
def _load_dashboard_config() -> dict:
    if _DEFAULT_CONFIG.exists():
        with open(_DEFAULT_CONFIG, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return raw.get("dashboard", {})
    return {}


def _cfg(key: str, default):
    return _load_dashboard_config().get(key, default)


# =============================================================================
# 数据加载（带缓存）
# =============================================================================

@st.cache_data(ttl=30)
def load_candidates() -> dict | None:
    candidates_file = Path(_cfg("candidates_file", "./data/candidates/candidates_latest.json"))
    if not candidates_file.exists():
        return None
    with open(candidates_file, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=300)
def load_kline(code: str) -> pd.DataFrame:
    """从缓存目录读取股票 K 线数据。"""
    adjust    = _cfg("adjust", "qfq")
    cache_dir = Path(_cfg("cache_dir", "./data/cache"))
    fpath     = cache_dir / f"{code}_{adjust}.csv"

    if not fpath.exists():
        return pd.DataFrame()

    df = pd.read_csv(fpath)
    df.columns = [c.lower() for c in df.columns]

    rename_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low",  "成交量": "volume",
    }
    df = df.rename(columns=rename_map)

    if "date" not in df.columns:
        df = df.reset_index()
        if "index" in df.columns:
            df = df.rename(columns={"index": "date"})

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    for col in ["open", "close", "high", "low", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.set_index("date").sort_index()

    chart_days = int(_cfg("chart_days", 120))
    return df.tail(chart_days)


# =============================================================================
# 图表绘制
# =============================================================================

def _build_kline_chart(df: pd.DataFrame, title: str, candidate: dict) -> go.Figure:
    """绘制 K 线图（含知行均线、成交量、KDJ 三面板）。"""
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="暂无数据", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font={"size": 20})
        return fig

    close  = df["close"]
    ma14   = close.rolling(14,  min_periods=1).mean()
    ma28   = close.rolling(28,  min_periods=1).mean()
    ma57   = close.rolling(57,  min_periods=1).mean()
    ma114  = close.rolling(114, min_periods=1).mean()

    # KDJ
    low_n  = df["low"].rolling(9, min_periods=1).min()
    high_n = df["high"].rolling(9, min_periods=1).max()
    rsv    = (close - low_n) / (high_n - low_n + 1e-9) * 100
    K_val  = rsv.ewm(com=2, adjust=False).mean()
    D_val  = K_val.ewm(com=2, adjust=False).mean()
    J_val  = 3 * K_val - 2 * D_val

    colors = ["#EF5350" if c >= o else "#26A69A"
              for c, o in zip(df["close"], df["open"])]

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.60, 0.20, 0.20],
        vertical_spacing=0.02,
        subplot_titles=(title, "成交量", "KDJ"),
    )

    # ── K 线 ──────────────────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        increasing_line_color="#EF5350",
        decreasing_line_color="#26A69A",
        name="K线",
    ), row=1, col=1)

    ma_styles = [
        (ma14,  "MA14",  "#FF9800", 1.2),
        (ma28,  "MA28",  "#2196F3", 1.2),
        (ma57,  "MA57",  "#9C27B0", 1.2),
        (ma114, "MA114", "#F44336", 1.5),
    ]
    for ma_series, ma_name, color, width in ma_styles:
        fig.add_trace(go.Scatter(
            x=df.index, y=ma_series, name=ma_name,
            line={"color": color, "width": width}, mode="lines",
        ), row=1, col=1)

    # 标注选股日期
    pick_date = candidate.get("date", "")
    if pick_date:
        pick_ts = pd.Timestamp(pick_date)
        if pick_ts in df.index:
            pick_close = df.loc[pick_ts, "close"]
            fig.add_vline(x=pick_ts, line_dash="dash",
                          line_color="yellow", line_width=1.5, row=1, col=1)
            fig.add_annotation(
                x=pick_ts, y=pick_close, text="选股日",
                showarrow=True, arrowhead=2, arrowcolor="yellow",
                font={"color": "yellow", "size": 10}, row=1, col=1,
            )

    # ── 成交量 ────────────────────────────────────────────────────────────────
    if "volume" in df.columns:
        fig.add_trace(go.Bar(
            x=df.index, y=df["volume"],
            marker_color=colors, name="成交量", showlegend=False,
        ), row=2, col=1)

    # ── KDJ ──────────────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(x=df.index, y=K_val, name="K", line={"color": "#FFC107", "width": 1}), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=D_val, name="D", line={"color": "#2196F3", "width": 1}), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=J_val, name="J", line={"color": "#EF5350", "width": 1}), row=3, col=1)
    # 超卖阈值线
    fig.add_hline(y=15, line_dash="dot", line_color="gray", row=3, col=1)

    fig.update_layout(
        height=700,
        template="plotly_dark",
        showlegend=True,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#1E1E1E",
        plot_bgcolor="#1E1E1E",
        font={"color": "#CCCCCC"},
        margin={"t": 40, "b": 20, "l": 60, "r": 20},
        legend={"orientation": "h", "y": 1.02, "x": 0},
    )

    return fig


# =============================================================================
# 页面主体
# =============================================================================

def main() -> None:
    st.set_page_config(
        page_title=_cfg("title", "量化选股 · B1策略看盘"),
        page_icon="📈",
        layout="wide",
    )

    st.title(_cfg("title", "量化选股 · B1策略看盘"))

    # ── 加载候选数据 ─────────────────────────────────────────────────────────
    raw = load_candidates()

    if raw is None:
        st.error("找不到候选文件，请先运行选股程序生成候选列表。")
        st.code("python run_all.py --skip-fetch", language="bash")
        st.stop()

    candidates  = raw.get("candidates", [])
    pick_date   = raw.get("pick_date",  "未知")
    meta        = raw.get("meta",       {})

    # ── 侧边栏 ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("筛选与控制")
        st.metric("选股日期", pick_date)
        st.metric("候选数量", f"{len(candidates)} 只")
        st.metric("扫描数量", f"{meta.get('scanned', '-')} 只")

        st.divider()
        j_max = st.slider("J 值上限", min_value=-20, max_value=20, value=15, step=1)

        if st.button("🔄 刷新数据", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # ── 过滤候选列表 ─────────────────────────────────────────────────────────
    filtered = [c for c in candidates if c.get("J", 99) <= j_max]

    if not filtered:
        st.warning(f"当前筛选条件（J ≤ {j_max}）下无候选股票。")
        st.stop()

    # ── 候选列表表格 ─────────────────────────────────────────────────────────
    st.subheader(f"候选股票列表（{len(filtered)} 只）")

    table_data = []
    for c in filtered:
        table_data.append({
            "代码":   c.get("code", ""),
            "名称":   c.get("name", ""),
            "收盘价":  round(c.get("close", 0), 2),
            "J值":    round(c.get("J",     0), 1),
            "K值":    round(c.get("K",     0), 1),
            "D值":    round(c.get("D",     0), 1),
            "MA14":   round(c.get("ma14",  0), 2),
            "MA28":   round(c.get("ma28",  0), 2),
            "MA57":   round(c.get("ma57",  0), 2),
            "MA114":  round(c.get("ma114", 0), 2),
            "均线排列": "✅" if c.get("zx_aligned")     else "❌",
            "周线确认": "✅" if c.get("weekly_aligned")  else "❌",
        })

    df_table = pd.DataFrame(table_data)
    st.dataframe(
        df_table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "代码":   st.column_config.TextColumn("代码",   width=80),
            "名称":   st.column_config.TextColumn("名称",   width=90),
            "收盘价":  st.column_config.NumberColumn("收盘价",  format="%.2f"),
            "J值":    st.column_config.NumberColumn("J值",    format="%.1f"),
        },
    )

    # ── K 线图 ───────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("K 线图（含知行均线 / 成交量 / KDJ）")

    options = [f"{c.get('code')}  {c.get('name', '')}  J={c.get('J', 0):.1f}"
               for c in filtered]
    selected_label = st.selectbox("选择股票", options, index=0)
    selected_code  = selected_label.split()[0] if selected_label else None

    if selected_code:
        selected_cand = next((c for c in filtered if c.get("code") == selected_code), {})
        kline_df = load_kline(selected_code)

        if kline_df.empty:
            st.warning(f"未找到 {selected_code} 的 K 线数据，请先运行数据拉取。")
        else:
            chart_title = f"{selected_cand.get('name', selected_code)}（{selected_code}）"
            fig = _build_kline_chart(kline_df, chart_title, selected_cand)
            st.plotly_chart(fig, use_container_width=True)

            # 指标摘要
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("收盘价",  f"{selected_cand.get('close', 0):.2f}")
            col2.metric("J 值",    f"{selected_cand.get('J', 0):.1f}")
            col3.metric("MA14",    f"{selected_cand.get('ma14',  0):.2f}")
            col4.metric("MA57",    f"{selected_cand.get('ma57',  0):.2f}")
            col5.metric("MA114",   f"{selected_cand.get('ma114', 0):.2f}")


if __name__ == "__main__":
    main()
