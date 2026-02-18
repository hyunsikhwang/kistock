from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta
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

PERIOD_LABELS = {
    "1m": "1개월",
    "3m": "3개월",
    "6m": "6개월",
    "1y": "1년",
    "3y": "3년",
}
PERIOD_TO_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 365 * 3}


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
        overflow: visible !important;
        text-overflow: unset !important;
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


def fmt_text(value: Any) -> str:
    return "-" if value is None or value == "" else str(value)


def fmt_money_kr(value: Any) -> str:
    num = to_float(value)
    if num is None:
        return "-"
    abs_num = abs(num)
    if abs_num >= 1_0000_0000_0000:
        return f"{num / 1_0000_0000_0000:.2f}조"
    if abs_num >= 1_0000_0000:
        return f"{num / 1_0000_0000:.2f}억"
    if abs_num >= 1_0000:
        return f"{num / 1_0000:.2f}만"
    return f"{num:,.0f}"


def fmt_delta_compact(change: Any, rate: Any) -> str:
    c = to_float(change)
    r = to_float(rate)
    if c is None and r is None:
        return "-"
    if c is None:
        return f"{fmt_num(r, 2)}%"
    if r is None:
        return f"{fmt_num(c)}원"
    return f"{fmt_num(c)}원 ({fmt_num(r, 2)}%)"


def build_kv_df(items: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame([{"항목": k, "값": v} for k, v in items])


def classify_relative_level(value: Any, baseline: Any) -> tuple[str, str]:
    v = to_float(value)
    b = to_float(baseline)
    if v is None or b is None or b <= 0:
        return "N/A", "#64748b"
    ratio = v / b
    if ratio <= 0.8:
        return "저", "#2563eb"
    if ratio <= 1.2:
        return "중", "#16a34a"
    return "고", "#ef4444"


@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def load_market_fundamental(date: str, market: str) -> pd.DataFrame:
    required_cols = ["ticker", "PER", "PBR"]

    try:
        base_dt = datetime.strptime(date, "%Y%m%d")
    except ValueError:
        base_dt = datetime.now()

    for offset in range(0, 7):
        target = (base_dt - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            df = krx_stock.get_market_fundamental_by_ticker(date=target, market=market)
        except Exception:
            continue
        if df is None or df.empty:
            continue

        out = df.reset_index().rename(columns={"티커": "ticker"})
        if "ticker" not in out.columns and len(out.columns) > 0:
            out = out.rename(columns={out.columns[0]: "ticker"})
        if "PER" not in out.columns:
            out["PER"] = None
        if "PBR" not in out.columns:
            out["PBR"] = None
        return out.reindex(columns=required_cols)

    return pd.DataFrame(columns=required_cols)


@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def resolve_sector_peer_tickers(sector_name: str, market: str) -> list[str]:
    if not sector_name:
        return []

    market_token = "KOSPI" if "KOSPI" in market.upper() else "KOSDAQ"
    try:
        index_tickers = krx_stock.get_index_ticker_list(market=market_token)
    except Exception:
        return []

    normalized = sector_name.replace(" ", "")
    target_index = None
    for idx in index_tickers:
        idx_name = str(krx_stock.get_index_ticker_name(idx)).replace(" ", "")
        if normalized and normalized in idx_name:
            target_index = idx
            break

    if target_index is None:
        return []

    try:
        peers = krx_stock.get_index_portfolio_deposit_file(target_index)
        return [str(x) for x in peers]
    except Exception:
        return []


def get_peer_valuation_baseline(sector_name: str, market: str) -> tuple[float | None, float | None, str]:
    market_token = "KOSPI" if "KOSPI" in market.upper() else "KOSDAQ"
    today = datetime.now().strftime("%Y%m%d")
    funda = load_market_fundamental(today, market_token)
    if funda.empty:
        return None, None, "기준 없음"

    peers = resolve_sector_peer_tickers(sector_name, market_token)
    if peers:
        peer_df = funda[funda["ticker"].isin(peers)].copy()
        if not peer_df.empty:
            peer_df["PER"] = pd.to_numeric(peer_df["PER"], errors="coerce")
            peer_df["PBR"] = pd.to_numeric(peer_df["PBR"], errors="coerce")
            per_avg = peer_df.loc[peer_df["PER"] > 0, "PER"].mean()
            pbr_avg = peer_df.loc[peer_df["PBR"] > 0, "PBR"].mean()
            return to_float(per_avg), to_float(pbr_avg), "업종 평균"

    funda["PER"] = pd.to_numeric(funda["PER"], errors="coerce")
    funda["PBR"] = pd.to_numeric(funda["PBR"], errors="coerce")
    per_avg = funda.loc[funda["PER"] > 0, "PER"].mean()
    pbr_avg = funda.loc[funda["PBR"] > 0, "PBR"].mean()
    return to_float(per_avg), to_float(pbr_avg), f"{market_token} 평균"


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


def is_token_issue_rate_limited(exc: Exception) -> bool:
    text = repr(exc)
    signals = (
        "EGW00133",
        "접근토큰 발급 잠시 후 다시 시도하세요",
        "/oauth2/tokenP",
        "403",
        "Forbidden",
    )
    return all(signal in text for signal in ("EGW00133", "/oauth2/tokenP")) or (
        "EGW00133" in text and "Forbidden" in text
    ) or all(signal in text for signal in signals[:2])


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
    max_attempts: int = 3,
) -> tuple[Any, pd.DataFrame]:
    last_error: Exception | None = None
    token_rate_limit_wait_sec = 61

    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                get_kis_client.clear()
            kis = get_kis_client(secret_json, token_json)
            stock = kis.stock(symbol)
            quote = stock.quote()
            # period 문자열 해석 차이를 피하기 위해 긴 구간을 받고 날짜 기준으로 직접 필터링
            base_chart_df = normalize_chart_df(stock.chart("3y").df())
            base_chart_df = adjust_chart_for_split(base_chart_df)
            return quote, base_chart_df
        except Exception as e:
            last_error = e
            if is_token_issue_rate_limited(e):
                if attempt == max_attempts:
                    raise RuntimeError(
                        "접근토큰 발급 제한(1분 1회)에 걸렸습니다. 1분 후 다시 시도해 주세요."
                    ) from e
                time.sleep(token_rate_limit_wait_sec)
                continue
            if attempt == max_attempts or not is_transient_network_error(e):
                raise
            time.sleep(0.8 * attempt)

    raise RuntimeError(f"알 수 없는 조회 오류: {last_error}")


def filter_chart_by_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if df.empty:
        return df
    days = PERIOD_TO_DAYS.get(period, 365)
    work = df.copy()
    work["date"] = pd.to_datetime(work["date"])
    end_date = work["date"].max()
    start_date = end_date - pd.Timedelta(days=days)
    work = work[work["date"] >= start_date].copy()
    work["date"] = work["date"].dt.strftime("%Y-%m-%d")
    return work.reset_index(drop=True)


def adjust_chart_for_split(df: pd.DataFrame, threshold: float = 1.7) -> pd.DataFrame:
    if df.empty or len(df) < 3:
        return df

    work = df.copy().reset_index(drop=True)
    close = work["close"].astype(float).values
    factors = [1.0] * len(work)

    for i in range(1, len(work)):
        prev = close[i - 1]
        cur = close[i]
        if prev <= 0 or cur <= 0:
            continue
        ratio = cur / prev
        if ratio >= threshold or ratio <= (1.0 / threshold):
            for j in range(i):
                factors[j] *= ratio

    factor_s = pd.Series(factors)
    for col in ("open", "high", "low", "close"):
        work[col] = pd.to_numeric(work[col], errors="coerce") * factor_s
    return work


@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def load_krx_ticker_name_map() -> pd.DataFrame:
    required_cols = ["ticker", "name", "market"]
    market_params = (
        ("KOSPI", "stockMkt"),
        ("KOSDAQ", "kosdaqMkt"),
        ("KONEX", "konexMkt"),
    )
    rows: list[dict[str, str]] = []

    for market, market_type in market_params:
        url = (
            "https://kind.krx.co.kr/corpgeneral/corpList.do"
            f"?method=download&searchType=13&marketType={market_type}"
        )
        try:
            tables = pd.read_html(url, encoding="euc-kr")
        except Exception:
            continue
        if not tables:
            continue

        df = tables[0]
        if df is None or df.empty:
            continue
        if "회사명" not in df.columns or "종목코드" not in df.columns:
            continue

        work = df[["회사명", "종목코드"]].dropna().copy()
        work["ticker"] = work["종목코드"].astype(str).str.zfill(6)
        work["name"] = work["회사명"].astype(str).str.strip()
        work["market"] = market
        rows.extend(work[["ticker", "name", "market"]].to_dict(orient="records"))

    if not rows:
        return pd.DataFrame(columns=required_cols)

    out = pd.DataFrame(rows).drop_duplicates(subset=["ticker"], keep="first")
    return out.reindex(columns=required_cols)


def resolve_symbol_input(query: str) -> tuple[str | None, pd.DataFrame, str]:
    token = query.strip()
    if not token:
        return None, pd.DataFrame(), "empty"

    required_cols = ["ticker", "name", "market"]
    universe_loaded = True
    try:
        universe = load_krx_ticker_name_map()
    except Exception:
        universe_loaded = False
        universe = pd.DataFrame(columns=required_cols)
    else:
        universe = universe.reindex(columns=required_cols)

    if token.isdigit() and len(token) == 6:
        return token, universe[universe["ticker"].astype(str) == token].head(10), "resolved"

    if universe.empty:
        return None, pd.DataFrame(), ("universe_unavailable" if not universe_loaded else "not_found")

    token_key = "".join(token.split()).lower()
    universe = universe.copy()
    universe["name"] = universe["name"].astype(str)
    universe["name_key"] = universe["name"].str.replace(r"\s+", "", regex=True).str.lower()

    exact = universe[universe["name"] == token]
    if len(exact) == 1:
        return str(exact.iloc[0]["ticker"]), exact[required_cols], "resolved"
    if len(exact) > 1:
        return None, exact[required_cols].head(20), "ambiguous"

    exact_key = universe[universe["name_key"] == token_key]
    if len(exact_key) == 1:
        return str(exact_key.iloc[0]["ticker"]), exact_key[required_cols], "resolved"
    if len(exact_key) > 1:
        return None, exact_key[required_cols].head(20), "ambiguous"

    contains = universe[universe["name"].str.contains(token, case=False, na=False, regex=False)]
    if contains.empty and token_key:
        contains = universe[universe["name_key"].str.contains(token_key, na=False, regex=False)]
    if len(contains) == 1:
        return str(contains.iloc[0]["ticker"]), contains[required_cols], "resolved"
    if len(contains) > 1:
        return None, contains[required_cols].head(20), "ambiguous"
    return None, pd.DataFrame(columns=required_cols), "not_found"


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
            return opts.AxisOpts(
                is_scale=True,
                splitline_opts=opts.SplitLineOpts(is_show=True),
                axislabel_opts=opts.LabelOpts(formatter="{value}", margin=12),
            )
        except TypeError:
            try:
                return opts.AxisOpts(
                    scale=True,
                    splitline_opts=opts.SplitLineOpts(is_show=True),
                    axislabel_opts=opts.LabelOpts(formatter="{value}", margin=12),
                )
            except TypeError:
                return opts.AxisOpts(
                    splitline_opts=opts.SplitLineOpts(is_show=True),
                    axislabel_opts=opts.LabelOpts(formatter="{value}", margin=12),
                )

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
    grid.add(kline, grid_opts=opts.GridOpts(pos_left="11%", pos_right="4%", pos_top="5%", height="65%"))
    grid.add(bar, grid_opts=opts.GridOpts(pos_left="11%", pos_right="4%", pos_top="74%", height="18%"))
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

with st.container(border=True):
    with st.form("search_form", clear_on_submit=False):
        f1, f2, f3 = st.columns([2.6, 1.2, 0.8])
        with f1:
            symbol_input = st.text_input("종목명 또는 종목코드", value="삼성전자").strip()
        with f2:
            period = st.selectbox(
                "차트 기간",
                list(PERIOD_LABELS.keys()),
                index=3,
                format_func=lambda x: PERIOD_LABELS.get(x, x),
            )
        with f3:
            st.markdown("<div style='height:1.65rem;'></div>", unsafe_allow_html=True)
            submitted = st.form_submit_button("시세 조회", type="primary", use_container_width=True)
    st.markdown(
        f'<div class="hint">조회 시각: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>',
        unsafe_allow_html=True,
    )

with st.expander("보안 설정 안내", expanded=False):
    render_security_guide()

if not submitted:
    st.info("상단에서 종목명(또는 종목코드)과 기간을 선택한 뒤 `시세 조회`를 눌러주세요.")
    st.stop()

symbol, candidates, resolve_state = resolve_symbol_input(symbol_input)
if symbol is None:
    if resolve_state == "universe_unavailable":
        st.warning("종목명 DB를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
    elif candidates.empty:
        st.warning("종목을 찾지 못했습니다. 정확한 종목명 또는 6자리 종목코드를 입력해 주세요.")
    else:
        st.warning("동일/유사한 종목명이 여러 개입니다. 아래 후보 중 정확한 종목명을 입력해 주세요.")
        st.dataframe(candidates, use_container_width=True, hide_index=True)
    st.stop()

secret_json = json.dumps(credentials["secret"], ensure_ascii=False)
token_json = json.dumps(credentials["token"], ensure_ascii=False) if credentials["token"] else None

if "quote_cache" not in st.session_state:
    st.session_state["quote_cache"] = {}

cache = st.session_state["quote_cache"]
cached = cache.get(symbol)
cache_hit = False

try:
    if cached and "quote" in cached and "base_chart_df" in cached:
        quote = cached["quote"]
        base_chart_df = cached["base_chart_df"]
        cache_hit = True
    else:
        with st.spinner("KIS API에 연결 중입니다..."):
            quote, base_chart_df = fetch_quote_and_chart(secret_json, token_json, symbol, max_attempts=3)
        cache[symbol] = {"quote": quote, "base_chart_df": base_chart_df, "cached_at": datetime.now()}
except Exception as e:
    st.error(f"조회 중 오류가 발생했습니다: {e}")
    st.stop()

chart_df = filter_chart_by_period(base_chart_df, period)

if cache_hit:
    st.caption("동일 종목 재조회: API 재호출 없이 캐시된 3년 차트로 기간만 재계산했습니다.")

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
market = get_path_attr(quote, "market")
sector_name = get_path_attr(quote, "sector_name")
prev_price = get_path_attr(quote, "prev_price")
open_price = get_path_attr(quote, "open")
high_price = get_path_attr(quote, "high")
low_price = get_path_attr(quote, "low")
high_limit = get_path_attr(quote, "high_limit")
low_limit = get_path_attr(quote, "low_limit")
sign_name = get_path_attr(quote, "sign_name")
currency = get_path_attr(quote, "currency")
exchange_rate = get_path_attr(quote, "exchange_rate")
risk = get_path_attr(quote, "risk")
halt = get_path_attr(quote, "halt")
overbought = get_path_attr(quote, "overbought")
unit = get_path_attr(quote, "unit")
tick = get_path_attr(quote, "tick")
decimal_places = get_path_attr(quote, "decimal_places")
w52_high = get_path_attr(quote, "indicator.week52_high")
w52_low = get_path_attr(quote, "indicator.week52_low")
w52_high_date = get_path_attr(quote, "indicator.week52_high_date")
w52_low_date = get_path_attr(quote, "indicator.week52_low_date")
per_base, pbr_base, base_label = get_peer_valuation_baseline(fmt_text(sector_name), fmt_text(market))
per_level, per_color = classify_relative_level(per, per_base)
pbr_level, pbr_color = classify_relative_level(pbr, pbr_base)
st.markdown(f"## {fmt_text(name)} ({symbol})")
st.caption(f"{fmt_text(sector_name)} | {fmt_text(market)} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 기준")

k1, k2, k3, k4 = st.columns(4)
k1.metric("현재가", f"{fmt_num(price)}원", fmt_delta_compact(change, rate))
k2.metric("시가총액", fmt_money_kr(market_cap), f"거래대금 {fmt_money_kr(amount)}")
k3.metric("PER / PBR", f"{fmt_num(per, 2)} / {fmt_num(pbr, 2)}", f"EPS {fmt_num(eps)}")
k4.metric("52주 범위", f"{fmt_num(w52_low)} ~ {fmt_num(w52_high)}", f"BPS {fmt_num(bps)}")

st.markdown(
    f"""
<div class="card" style="margin-top: .35rem;">
  <div style="font-size:.84rem;color:#64748b;margin-bottom:.35rem;">Valuation 상대 수준 ({base_label} 기준)</div>
  <div style="display:flex;gap:.6rem;flex-wrap:wrap;">
    <span style="padding:.2rem .55rem;border-radius:999px;background:{per_color};color:#fff;font-size:.82rem;">PER: {per_level}</span>
    <span style="padding:.2rem .55rem;border-radius:999px;background:{pbr_color};color:#fff;font-size:.82rem;">PBR: {pbr_level}</span>
    <span style="padding:.2rem .55rem;border-radius:999px;background:#e2e8f0;color:#0f172a;font-size:.82rem;">
      평균 PER/PBR {fmt_num(per_base,2)} / {fmt_num(pbr_base,2)}
    </span>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

range_low = to_float(w52_low)
range_high = to_float(w52_high)
current = to_float(price)
period_low = to_float(chart_df["low"].min()) if not chart_df.empty else None
period_high = to_float(chart_df["high"].max()) if not chart_df.empty else None

if period_low is not None and period_high is not None and current is not None and period_high > period_low:
    pos = (current - period_low) / (period_high - period_low)
    pos = max(0.0, min(1.0, pos))
    st.markdown(f"**선택 기간({PERIOD_LABELS.get(period, period)}) 밴드 내 현재 위치 (분할 보정)**")
    st.progress(pos, text=f"기간 저가 대비 {pos * 100:.1f}% 지점")
elif range_low is not None and range_high is not None and current is not None and range_high > range_low:
    pos = (current - range_low) / (range_high - range_low)
    pos = max(0.0, min(1.0, pos))
    st.markdown("**52주 밴드 내 현재 위치**")
    st.progress(pos, text=f"52주 저가 대비 {pos * 100:.1f}% 지점")

st_pyecharts(make_kline_chart(chart_df, symbol, name), height="640px")

tab1, tab2, tab3 = st.tabs(["Valuation", "Price Action", "Risk / Market"])

with tab1:
    left, right = st.columns(2)
    left.dataframe(
        build_kv_df(
            [
                ("PER", fmt_num(per, 2)),
                ("PBR", fmt_num(pbr, 2)),
                ("EPS", fmt_num(eps)),
                ("BPS", fmt_num(bps)),
                ("시가총액", fmt_num(market_cap)),
                ("통화 / 환율", f"{fmt_text(currency)} / {fmt_num(exchange_rate, 4)}"),
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    right.dataframe(
        build_kv_df(
            [
                ("52주 고가", f"{fmt_num(w52_high)} ({fmt_text(w52_high_date)[:10]})"),
                ("52주 저가", f"{fmt_num(w52_low)} ({fmt_text(w52_low_date)[:10]})"),
                ("전일종가", fmt_num(prev_price)),
                ("상한가 / 하한가", f"{fmt_num(high_limit)} / {fmt_num(low_limit)}"),
                ("업종", fmt_text(sector_name)),
                ("시장", fmt_text(market)),
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

with tab2:
    st.dataframe(
        build_kv_df(
            [
                ("현재가", fmt_num(price)),
                ("전일대비", f"{fmt_num(change)} ({fmt_num(rate, 2)}%)"),
                ("대비부호", fmt_text(sign_name)),
                ("시가 / 고가 / 저가", f"{fmt_num(open_price)} / {fmt_num(high_price)} / {fmt_num(low_price)}"),
                ("거래량", fmt_num(volume)),
                ("거래대금", fmt_num(amount)),
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

with tab3:
    st.dataframe(
        build_kv_df(
            [
                ("위험도", fmt_text(risk)),
                ("거래정지", fmt_text(halt)),
                ("단기과열구분", fmt_text(overbought)),
                ("거래단위", fmt_text(unit)),
                ("호가단위", fmt_text(tick)),
                ("소수점자리수", fmt_text(decimal_places)),
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
