from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import streamlit as st
from pyecharts import options as opts
from pyecharts.charts import Bar, Grid, Kline
from streamlit_echarts import st_pyecharts

from pykis.kis import PyKis


st.set_page_config(
    page_title="KIS Real-time K-Stock",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    [data-testid="stHeader"], #MainMenu, footer, .stDeployButton { display: none !important; }
    .block-container { padding-top: 1rem !important; max-width: 1100px !important; }
    .stApp {
        background: linear-gradient(180deg, #fbfcfd 0%, #ffffff 48%);
        color: #0f172a;
    }
    .hero {
        padding: 1.35rem 0 1.1rem 0;
        border-bottom: 1px solid #edf0f3;
        margin-bottom: 1.2rem;
    }
    .hero-title {
        font-size: 2rem;
        font-weight: 700;
        letter-spacing: -0.4px;
        color: #0b1220;
        margin-bottom: .15rem;
    }
    .hero-sub {
        color: #64748b;
        font-size: .95rem;
    }
    .card {
        background: #ffffff;
        border: 1px solid #e8ecf2;
        border-radius: 14px;
        padding: 0.9rem 1rem;
    }
    .hint {
        color: #64748b;
        font-size: .86rem;
    }
</style>
""",
    unsafe_allow_html=True,
)


def get_path_attr(obj: Any, path: str, default: Any = None) -> Any:
    cur = obj
    for part in path.split("."):
        if cur is None or not hasattr(cur, part):
            return default
        cur = getattr(cur, part)
    return cur


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fmt_num(value: Any, digit: int = 0) -> str:
    num = to_float(value)
    if num is None:
        return "-"
    return f"{num:,.{digit}f}"


def load_kis_profile() -> dict[str, dict[str, str]]:
    if "kis" not in st.secrets:
        return {}

    root = st.secrets["kis"]
    profiles: dict[str, dict[str, str]] = {}

    required = ("id", "appkey", "secretkey", "account")
    if all(root.get(k) for k in required):
        profiles["실전"] = {
            "id": root["id"],
            "appkey": root["appkey"],
            "secretkey": root["secretkey"],
            "account": root["account"],
        }

    v_required = ("virtual_id", "virtual_appkey", "virtual_secretkey", "virtual_account")
    if all(root.get(k) for k in v_required):
        profiles["모의"] = {
            "id": root["virtual_id"],
            "appkey": root["virtual_appkey"],
            "secretkey": root["virtual_secretkey"],
            "account": root["virtual_account"],
        }
    return profiles


@st.cache_resource(show_spinner=False)
def get_kis_client(profile_name: str, profile_json: str) -> PyKis:
    payload = json.loads(profile_json)
    fd, tmp_path = tempfile.mkstemp(prefix=f"kis_{profile_name}_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return PyKis(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def normalize_chart_df(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()

    if work.index.name and work.index.name.lower() in {"date", "datetime", "time"}:
        work = work.reset_index()
    elif "date" not in [c.lower() for c in work.columns]:
        work = work.reset_index()

    lowered = {c.lower(): c for c in work.columns}

    def pick(*names: str) -> str:
        for name in names:
            if name in lowered:
                return lowered[name]
        raise KeyError(f"column not found: {names}")

    c_date = pick("date", "datetime", "time", "index")
    c_open = pick("open", "stck_oprc")
    c_high = pick("high", "stck_hgpr")
    c_low = pick("low", "stck_lwpr")
    c_close = pick("close", "stck_prpr")
    c_volume = pick("volume", "acml_vol")

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(work[c_date]).dt.strftime("%Y-%m-%d"),
            "open": pd.to_numeric(work[c_open], errors="coerce"),
            "high": pd.to_numeric(work[c_high], errors="coerce"),
            "low": pd.to_numeric(work[c_low], errors="coerce"),
            "close": pd.to_numeric(work[c_close], errors="coerce"),
            "volume": pd.to_numeric(work[c_volume], errors="coerce"),
        }
    )
    out = out.dropna(subset=["date", "open", "high", "low", "close"]).reset_index(drop=True)
    return out


def make_kline_chart(df: pd.DataFrame, symbol: str, name: str) -> Grid:
    x_data = df["date"].tolist()
    k_data = df[["open", "close", "low", "high"]].round(2).values.tolist()
    volume_data = df["volume"].fillna(0).astype(float).tolist()

    kline = (
        Kline()
        .add_xaxis(x_data)
        .add_yaxis(
            series_name=f"{name} ({symbol})",
            y_axis=k_data,
            itemstyle_opts=opts.ItemStyleOpts(
                color="#ef4444",
                color0="#2563eb",
                border_color="#ef4444",
                border_color0="#2563eb",
            ),
        )
        .set_global_opts(
            xaxis_opts=opts.AxisOpts(type_="category"),
            yaxis_opts=opts.AxisOpts(scale=True, splitline_opts=opts.SplitLineOpts(is_show=True)),
            legend_opts=opts.LegendOpts(is_show=False),
            datazoom_opts=[
                opts.DataZoomOpts(type_="inside", range_start=60, range_end=100),
                opts.DataZoomOpts(type_="slider", pos_bottom="2%"),
            ],
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
        )
    )

    bar = (
        Bar()
        .add_xaxis(x_data)
        .add_yaxis("거래량", volume_data, xaxis_index=1, yaxis_index=1, label_opts=opts.LabelOpts(is_show=False))
        .set_global_opts(
            xaxis_opts=opts.AxisOpts(
                type_="category",
                grid_index=1,
                axislabel_opts=opts.LabelOpts(is_show=False),
            ),
            yaxis_opts=opts.AxisOpts(
                grid_index=1,
                split_number=2,
                axislabel_opts=opts.LabelOpts(formatter="{value}"),
                splitline_opts=opts.SplitLineOpts(is_show=False),
            ),
            legend_opts=opts.LegendOpts(is_show=False),
        )
    )

    grid = Grid(init_opts=opts.InitOpts(height="620px"))
    grid.add(kline, grid_opts=opts.GridOpts(pos_left="7%", pos_right="3%", pos_top="5%", height="65%"))
    grid.add(bar, grid_opts=opts.GridOpts(pos_left="7%", pos_right="3%", pos_top="74%", height="18%"))
    return grid


def render_security_guide() -> None:
    st.markdown(
        """
```toml
# .streamlit/secrets.toml (로컬 전용, 절대 커밋 금지)
[kis]
id = "KIS_LOGIN_ID"
appkey = "KIS_APP_KEY"
secretkey = "KIS_SECRET_KEY"
account = "12345678-01"
virtual_id = "KIS_VIRTUAL_ID"
virtual_appkey = "KIS_VIRTUAL_APP_KEY"
virtual_secretkey = "KIS_VIRTUAL_SECRET_KEY"
virtual_account = "87654321-01"
```
"""
    )
    st.markdown(
        """
<div class="hint">
1) GitHub에는 <code>.streamlit/secrets.toml</code>, <code>secret.json</code>, <code>token.json</code>를 올리지 않습니다.<br/>
2) Streamlit Cloud 대시보드의 <b>Settings → Secrets</b>에만 저장합니다.<br/>
3) 앱 코드에서는 <code>st.secrets</code>만 사용하고 파일/로그 출력으로 남기지 않습니다.
</div>
""",
        unsafe_allow_html=True,
    )


st.markdown(
    """
<div class="hero">
  <div class="hero-title">KIS Real-time K-Stock</div>
  <div class="hero-sub">한국투자증권 Open API 기반 실시간 시세 · pyecharts 차트</div>
</div>
""",
    unsafe_allow_html=True,
)

profiles = load_kis_profile()

if not profiles:
    st.error("`st.secrets['kis']` 설정이 없습니다. 아래 보안 설정 예시를 참고해 주세요.")
    render_security_guide()
    st.stop()

col_left, col_right = st.columns([1.05, 1.95], gap="large")

with col_left:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    mode = st.selectbox("계정 모드", list(profiles.keys()), index=0)
    symbol = st.text_input("종목코드(6자리)", value="005930").strip().upper()
    period = st.selectbox("차트 기간", ["1m", "3m", "6m", "1y", "3y"], index=3)
    submitted = st.button("시세 조회", type="primary", use_container_width=True)
    st.markdown(
        f'<div class="hint">조회 시각: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>',
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("보안 설정 안내", expanded=False):
        render_security_guide()

with col_right:
    if not submitted:
        st.info("좌측에서 종목코드와 기간을 선택한 뒤 `시세 조회`를 눌러주세요.")
        st.stop()

    if not symbol or not symbol.isalnum():
        st.warning("올바른 종목코드를 입력해 주세요. (예: 005930, 000660)")
        st.stop()

    profile_json = json.dumps(profiles[mode], ensure_ascii=False)

    try:
        with st.spinner("KIS API에 연결 중입니다..."):
            kis = get_kis_client(mode, profile_json)
            stock = kis.stock(symbol)
            quote = stock.quote()
            chart_df = normalize_chart_df(stock.chart(period).df())
    except Exception as e:
        st.error(f"조회 중 오류가 발생했습니다: {e}")
        st.stop()

    name = get_path_attr(quote, "name", symbol)
    price = get_path_attr(quote, "price")
    change = get_path_attr(quote, "change")
    rate = get_path_attr(quote, "rate")
    volume = get_path_attr(quote, "volume")
    amount = get_path_attr(quote, "amount")
    market_cap = get_path_attr(quote, "market_cap")
    per = get_path_attr(quote, "indicator.per")
    pbr = get_path_attr(quote, "indicator.pbr")
    eps = get_path_attr(quote, "indicator.eps")
    bps = get_path_attr(quote, "indicator.bps")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("현재가", f"{fmt_num(price)}원", f"{fmt_num(change)} ({fmt_num(rate, 2)}%)")
    m2.metric("거래량", fmt_num(volume))
    m3.metric("거래대금", fmt_num(amount))
    m4.metric("시가총액", fmt_num(market_cap))

    st_pyecharts(make_kline_chart(chart_df, symbol, name), height="640px")

    st.markdown("### 종목 지표")
    t1, t2, t3, t4 = st.columns(4)
    t1.markdown(f"**PER**  \n{fmt_num(per, 2)}")
    t2.markdown(f"**PBR**  \n{fmt_num(pbr, 2)}")
    t3.markdown(f"**EPS**  \n{fmt_num(eps, 0)}")
    t4.markdown(f"**BPS**  \n{fmt_num(bps, 0)}")
