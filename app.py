from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from decimal import Decimal
from http.client import RemoteDisconnected
from typing import Any

import pandas as pd
import streamlit as st
from pykrx import stock as krx_stock
from pyecharts import options as opts
from pyecharts.charts import Bar, Grid, Kline
from streamlit_echarts import st_pyecharts

from pykis.kis import KisAccessToken, PyKis


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
    [data-testid="stMetricValue"] {
        font-size: 1.25rem !important;
        line-height: 1.2 !important;
        white-space: normal !important;
    }
    [data-testid="stMetricDelta"] {
        font-size: .9rem !important;
        white-space: normal !important;
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


def parse_json_or_table(raw: Any, field_name: str) -> dict[str, Any]:
    if raw is None:
        raise ValueError(f"{field_name} 값이 없습니다.")
    if isinstance(raw, str):
        return json.loads(raw)
    if hasattr(raw, "items"):
        return dict(raw.items())
    if isinstance(raw, dict):
        return raw
    raise ValueError(f"{field_name} 형식이 올바르지 않습니다.")


def normalize_secret_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "id": payload.get("id") or payload.get("kis_id"),
        "appkey": payload.get("appkey") or payload.get("kis_appkey"),
        "secretkey": payload.get("secretkey") or payload.get("kis_secretkey"),
        "account": payload.get("account") or payload.get("kis_account"),
    }
    if not all(normalized.values()):
        raise ValueError("secret 정보(id/appkey/secretkey/account)가 누락되었습니다.")
    return normalized


def load_kis_credentials() -> dict[str, dict[str, Any]]:
    if "kis" not in st.secrets:
        return {}

    root = st.secrets["kis"]

    raw_secret = root.get("secret")
    if raw_secret is None:
        raw_secret = {
            "id": root.get("id") or root.get("kis_id"),
            "appkey": root.get("appkey") or root.get("kis_appkey"),
            "secretkey": root.get("secretkey") or root.get("kis_secretkey"),
            "account": root.get("account") or root.get("kis_account"),
        }

    secret_payload = normalize_secret_payload(parse_json_or_table(raw_secret, "secret"))
    raw_token = root.get("token") or root.get("kis_token")
    token_payload = parse_token_payload(raw_token) if raw_token is not None else None
    return {"secret": secret_payload, "token": token_payload}


def parse_token_payload(raw_token: Any) -> dict[str, Any]:
    token = parse_json_or_table(raw_token, "token")

    # python-kis 환경별 토큰 키 이름 차이를 흡수
    if "access_token_token_expired" in token and "expires_at" not in token:
        token["expires_at"] = token["access_token_token_expired"]
    if "expired_at" in token and "expires_at" not in token:
        token["expires_at"] = token["expired_at"]
    if "token_type" not in token:
        token["token_type"] = "Bearer"

    return token


def is_transient_network_error(exc: Exception) -> bool:
    if isinstance(exc, (RemoteDisconnected, TimeoutError, ConnectionError, OSError)):
        return True
    text = repr(exc).lower()
    transient_signals = (
        "connection aborted",
        "remote end closed connection without response",
        "remotedisconnected",
        "read timed out",
        "connection reset by peer",
        "temporarily unavailable",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
    )
    return any(signal in text for signal in transient_signals)


@st.cache_resource(show_spinner=False)
def get_kis_client(secret_json: str, token_json: str | None) -> PyKis:
    secret_payload = normalize_secret_payload(json.loads(secret_json))
    token_payload = parse_token_payload(json.loads(token_json)) if token_json else None

    kis = PyKis(
        id=secret_payload["id"],
        account=secret_payload["account"],
        appkey=secret_payload["appkey"],
        secretkey=secret_payload["secretkey"],
        keep_token=True,
    )

    if token_payload:
        tfd, token_path = tempfile.mkstemp(prefix="kis_token_", suffix=".json")
        try:
            with os.fdopen(tfd, "w", encoding="utf-8") as tf:
                json.dump(token_payload, tf, ensure_ascii=False)
            kis.token = KisAccessToken.load(token_path)
        finally:
            try:
                os.remove(token_path)
            except OSError:
                pass
    return kis


def fetch_quote_and_chart(
    secret_json: str,
    token_json: str | None,
    symbol: str,
    period: str,
    max_attempts: int = 3,
) -> tuple[Any, pd.DataFrame]:
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                get_kis_client.clear()
            kis = get_kis_client(secret_json, token_json)
            stock = kis.stock(symbol)
            quote = stock.quote()
            chart_df = normalize_chart_df(stock.chart(period).df())
            return quote, chart_df
        except Exception as e:
            last_error = e
            if attempt == max_attempts or not is_transient_network_error(e):
                raise
            time.sleep(0.8 * attempt)

    raise RuntimeError(f"알 수 없는 조회 오류: {last_error}")


@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def load_krx_ticker_name_map() -> pd.DataFrame:
    today = datetime.now().strftime("%Y%m%d")
    rows: list[dict[str, str]] = []
    for market in ("KOSPI", "KOSDAQ", "KONEX"):
        tickers = krx_stock.get_market_ticker_list(date=today, market=market)
        for ticker in tickers:
            name = krx_stock.get_market_ticker_name(ticker)
            if name:
                rows.append({"ticker": ticker, "name": str(name), "market": market})
    return pd.DataFrame(rows)


def resolve_symbol_input(query: str) -> tuple[str | None, pd.DataFrame]:
    token = query.strip()
    if not token:
        return None, pd.DataFrame()

    try:
        universe = load_krx_ticker_name_map()
    except Exception:
        universe = pd.DataFrame(columns=["ticker", "name", "market"])

    if token.isdigit() and len(token) == 6:
        return token, universe[universe["ticker"] == token].head(10)

    if universe.empty:
        return None, pd.DataFrame()

    exact = universe[universe["name"] == token]
    if len(exact) == 1:
        return str(exact.iloc[0]["ticker"]), exact
    if len(exact) > 1:
        return None, exact.head(20)

    contains = universe[universe["name"].str.contains(token, case=False, na=False)]
    if len(contains) == 1:
        return str(contains.iloc[0]["ticker"]), contains
    return None, contains.head(20)


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

    def build_price_axis_opts() -> opts.AxisOpts:
        # pyecharts 버전별 파라미터 차이(scale vs is_scale) 호환
        try:
            return opts.AxisOpts(is_scale=True, splitline_opts=opts.SplitLineOpts(is_show=True))
        except TypeError:
            try:
                return opts.AxisOpts(scale=True, splitline_opts=opts.SplitLineOpts(is_show=True))
            except TypeError:
                return opts.AxisOpts(splitline_opts=opts.SplitLineOpts(is_show=True))

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
            yaxis_opts=build_price_axis_opts(),
            legend_opts=opts.LegendOpts(is_show=False),
            datazoom_opts=[
                opts.DataZoomOpts(type_="inside", range_start=0, range_end=100),
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
account = "12345678-01"
appkey = "KIS_APP_KEY"
secretkey = "KIS_SECRET_KEY"
# 선택: 기존 token.json 내용을 JSON 문자열로 그대로 넣으면 주입합니다.
token = '{"access_token":"...","access_token_token_expired":"2026-02-07 23:59:59","token_type":"Bearer","expires_in":86400}'
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

credentials = load_kis_credentials()

if not credentials:
    st.error("`st.secrets['kis']` 설정이 없습니다. 아래 보안 설정 예시를 참고해 주세요.")
    render_security_guide()
    st.stop()

col_left, col_right = st.columns([1.05, 1.95], gap="large")

with col_left:
    with st.container(border=True):
        symbol_input = st.text_input("종목명 또는 종목코드", value="삼성전자").strip()
        period = st.selectbox("차트 기간", ["1m", "3m", "6m", "1y", "3y"], index=3)
        submitted = st.button("시세 조회", type="primary", use_container_width=True)
        st.markdown(
            f'<div class="hint">조회 시각: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>',
            unsafe_allow_html=True,
        )

    with st.expander("보안 설정 안내", expanded=False):
        render_security_guide()

with col_right:
    if not submitted:
        st.info("좌측에서 종목명(또는 종목코드)과 기간을 선택한 뒤 `시세 조회`를 눌러주세요.")
        st.stop()

    symbol, candidates = resolve_symbol_input(symbol_input)
    if symbol is None:
        if candidates.empty:
            st.warning("종목을 찾지 못했습니다. 정확한 종목명 또는 6자리 종목코드를 입력해 주세요.")
        else:
            st.warning("동일/유사한 종목명이 여러 개입니다. 아래 후보 중 정확한 종목명을 입력해 주세요.")
            st.dataframe(candidates, use_container_width=True, hide_index=True)
        st.stop()

    secret_json = json.dumps(credentials["secret"], ensure_ascii=False)
    token_json = json.dumps(credentials["token"], ensure_ascii=False) if credentials["token"] else None

    try:
        with st.spinner("KIS API에 연결 중입니다..."):
            quote, chart_df = fetch_quote_and_chart(secret_json, token_json, symbol, period, max_attempts=3)
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

    m1, m2 = st.columns(2)
    m1.metric("현재가", f"{fmt_num(price)}원", f"{fmt_num(change)} ({fmt_num(rate, 2)}%)")
    m2.metric("거래량", fmt_num(volume))
    m3, m4 = st.columns(2)
    m3.metric("거래대금", fmt_num(amount))
    m4.metric("시가총액", fmt_num(market_cap))

    st_pyecharts(make_kline_chart(chart_df, symbol, name), height="640px")

    st.markdown("### 종목 지표")
    t1, t2, t3, t4 = st.columns(4)
    t1.markdown(f"**PER**  \n{fmt_num(per, 2)}")
    t2.markdown(f"**PBR**  \n{fmt_num(pbr, 2)}")
    t3.markdown(f"**EPS**  \n{fmt_num(eps, 0)}")
    t4.markdown(f"**BPS**  \n{fmt_num(bps, 0)}")
