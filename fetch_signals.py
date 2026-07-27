#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_signals.py — 미국주식 시그널 보드 데이터 수집기

signal_board.html 이 요구하는 11개 항목을 야후 파이낸스에서 자동으로 받아
CSV로 저장합니다. 저장된 CSV를 열어 전체 복사한 뒤, 보드의 [표 붙여넣기]에
그대로 붙여넣으면 끝입니다.

설치:  pip install yfinance pandas
실행:  python fetch_signals.py
       python fetch_signals.py --tickers NVDA,AAPL,MSFT --out 오늘.csv
       python fetch_signals.py --watchlist watchlist.csv

watchlist.csv 형식 (헤더 필수, 테마는 비워도 됨):
    ticker,theme
    NVDA,매그니피센트7
    ORCL,SW 주식
"""

import argparse
import csv
import sys
import time
from datetime import datetime

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    sys.exit("먼저 설치하세요:  pip install yfinance pandas")


# ─────────────────────────────────────────────────────────────
# 기본 워치리스트 — 여기를 본인 종목으로 바꾸면 됩니다
# ─────────────────────────────────────────────────────────────
DEFAULT_WATCHLIST = [
    # 매그니피센트7
    ("NVDA", "매그니피센트7"), ("AAPL", "매그니피센트7"), ("MSFT", "매그니피센트7"),
    ("GOOGL", "매그니피센트7"), ("AMZN", "매그니피센트7"), ("META", "매그니피센트7"),
    ("TSLA", "매그니피센트7"),
    # 반도체
    ("AVGO", "반도체"), ("AMD", "반도체"), ("ARM", "반도체"), ("QCOM", "반도체"),
    ("MU", "반도체"), ("TSM", "반도체"), ("LRCX", "반도체"), ("AMAT", "반도체"),
    # SW·클라우드
    ("ORCL", "SW 주식"), ("PLTR", "SW 주식"), ("CRM", "SW 주식"),
    ("NOW", "SW 주식"), ("SNOW", "SW 주식"),
    # 전력·원전
    ("NEE", "전력·원전"), ("CEG", "전력·원전"), ("SMR", "전력·원전"),
    ("VST", "전력·원전"), ("OKLO", "전력·원전"),
    # 금융
    ("JPM", "금융"), ("BAC", "금융"), ("GS", "금융"), ("V", "금융"), ("MA", "금융"),
    # 헬스케어
    ("LLY", "헬스케어"), ("UNH", "헬스케어"), ("JNJ", "헬스케어"),
    ("ABBV", "헬스케어"), ("ISRG", "헬스케어"),
    # 소비재
    ("COST", "소비재"), ("WMT", "소비재"), ("NKE", "소비재"),
    ("SBUX", "소비재"), ("MCD", "소비재"),
    # 방산·우주
    ("LMT", "방산·우주"), ("RTX", "방산·우주"), ("NOC", "방산·우주"), ("RKLB", "방산·우주"),
    # 에너지
    ("XOM", "에너지"), ("CVX", "에너지"), ("OXY", "에너지"),
    # 핀테크·크립토
    ("COIN", "핀테크·크립토"), ("HOOD", "핀테크·크립토"), ("PYPL", "핀테크·크립토"),
    # 미디어·통신
    ("NFLX", "미디어·통신"), ("DIS", "미디어·통신"), ("TMUS", "미디어·통신"),
    # 산업재
    ("CAT", "산업재"), ("DE", "산업재"), ("HON", "산업재"), ("GE", "산업재"),
    # ETF — 개별 PER·부채비율 등이 없어 대부분 결측(0점) 처리됩니다
    ("SPY", "ETF"), ("QQQ", "ETF"), ("SCHD", "ETF"),
]

# 3Y 평균 PER 직접 지정값 (선택).
# 야후는 과거 선행 PER을 주지 않아 스크립트가 '과거 실적 PER 3년 평균'으로
# 근사합니다. 직접 관리하는 값이 있으면 여기에 적으면 그 값이 우선합니다.
PER3Y_OVERRIDE = {
    # "NVDA": 38.5,
    # "MSFT": 30.2,
}


# ─────────────────────────────────────────────────────────────
# 지표 계산
# ─────────────────────────────────────────────────────────────
def rsi_wilder(close: pd.Series, period: int = 14):
    """와일더 방식 RSI. 보드가 쓰는 값과 같은 정의입니다."""
    if close is None or len(close) < period + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    last_gain = float(avg_gain.iloc[-1])
    last_loss = float(avg_loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return round(100 - 100 / (1 + rs), 1)


def per3y_from_history(tk: yf.Ticker, hist: pd.DataFrame):
    """
    최근 3개 회계연도의 '연평균 주가 ÷ 연간 EPS' 평균으로 3년 평균 PER을 근사합니다.
    선행 PER과는 정의가 달라 어디까지나 참고값입니다. 정확한 값을 쓰려면
    PER3Y_OVERRIDE 에 직접 입력하세요.
    """
    try:
        inc = tk.income_stmt
        if inc is None or inc.empty or hist is None or hist.empty:
            return None
        rows = {str(i).lower(): i for i in inc.index}
        eps_row = next((rows[k] for k in rows if "diluted eps" in k), None)
        if eps_row is None:
            return None

        pers = []
        for col in list(inc.columns)[:3]:          # 최근 3개 회계연도
            eps = inc.loc[eps_row, col]
            if pd.isna(eps) or float(eps) <= 0:
                continue
            year = pd.Timestamp(col).year
            window = hist.loc[hist.index.year == year, "Close"]
            if window.empty:
                continue
            pers.append(float(window.mean()) / float(eps))
        if not pers:
            return None
        return round(sum(pers) / len(pers), 2)
    except Exception:
        return None


def pct(a, b):
    """a 가 b 대비 몇 % 인지. 소수 첫째 자리."""
    if a is None or b in (None, 0):
        return None
    return round((a / b - 1) * 100, 1)


# ─────────────────────────────────────────────────────────────
# 종목 상세용 부가 데이터
#   보드 채점에는 안 쓰이고, 상세 탭에서 근거를 보여주는 데만 씁니다.
#   야후가 안 주는 항목은 조용히 비워 둡니다. 없으면 그 블록만 안 그립니다.
# ─────────────────────────────────────────────────────────────
def _pick(df, *names):
    """손익계산서/현금흐름표에서 이름이 비슷한 행을 찾아 줍니다."""
    if df is None or getattr(df, "empty", True):
        return None
    idx = {str(i).strip().lower(): i for i in df.index}
    for want in names:
        w = want.lower()
        for k, orig in idx.items():
            if k == w:
                return df.loc[orig]
    for want in names:
        w = want.lower()
        for k, orig in idx.items():
            if w in k:
                return df.loc[orig]
    return None


def _b(v):
    """달러를 10억 단위로. 소수 둘째 자리."""
    try:
        if v is None or pd.isna(v):
            return None
        return round(float(v) / 1e9, 2)
    except Exception:
        return None


def _label(ts, quarterly):
    try:
        t = pd.Timestamp(ts)
    except Exception:
        return str(ts)[:7]
    return f"{t.year % 100:02d}.{(t.month - 1) // 3 + 1}Q" if quarterly else f"FY{t.year}"


def _statement(df, quarterly, keys, limit):
    """재무제표 DataFrame 을 오래된 것부터 정렬한 리스트로."""
    if df is None or getattr(df, "empty", True):
        return []
    series = {k: _pick(df, *names) for k, names in keys.items()}
    cols = list(df.columns)[:limit]
    out = []
    for c in reversed(cols):
        rec = {"p": _label(c, quarterly)}
        got = False
        for k, s in series.items():
            v = _b(s.get(c)) if s is not None and c in s.index else None
            rec[k] = v
            if v is not None:
                got = True
        if got:
            out.append(rec)
    return out


def _estimates(tk, price, fwd_per):
    """컨센서스 예상 매출·EPS. yfinance 버전에 따라 없을 수 있습니다."""
    out = []
    try:
        rev = tk.revenue_estimate
        eps = tk.earnings_estimate
    except Exception:
        return out
    for tag, label in (("0y", "올해"), ("+1y", "내년")):
        rec = {"p": label}
        try:
            if rev is not None and tag in rev.index:
                rec["rev"] = _b(rev.loc[tag].get("avg"))
                g = rev.loc[tag].get("growth")
                rec["revG"] = round(float(g) * 100, 1) if g is not None and not pd.isna(g) else None
        except Exception:
            pass
        try:
            if eps is not None and tag in eps.index:
                e = eps.loc[tag].get("avg")
                rec["eps"] = round(float(e), 2) if e is not None and not pd.isna(e) else None
                g = eps.loc[tag].get("growth")
                rec["epsG"] = round(float(g) * 100, 1) if g is not None and not pd.isna(g) else None
        except Exception:
            pass
        # 지금 배수를 그대로 유지한다고 볼 때의 주가
        if rec.get("eps") and fwd_per and fwd_per > 0:
            rec["px"] = round(rec["eps"] * fwd_per, 2)
            rec["pxGap"] = pct(rec["px"], price)
        if any(rec.get(k) is not None for k in ("rev", "eps")):
            out.append(rec)
    return out


def fetch_valuation(info, hist, price, mcap, est):
    """밸류 비교 표 전용 지표. 채점에는 쓰이지 않습니다."""
    v = {}
    try:
        cl = hist["Close"]
        this_year = cl[cl.index.year == datetime.now().year]
        if len(this_year) and price:
            v["ytd"] = round((price / float(this_year.iloc[0]) - 1) * 100, 1)
    except Exception:
        pass

    def put(key, src, nd=2, mul=1):
        x = info.get(src)
        if x is not None:
            try:
                v[key] = round(float(x) * mul, nd)
            except Exception:
                pass

    put("per",  "trailingPE")
    put("psr",  "priceToSalesTrailing12Months")
    put("ev",   "enterpriseToEbitda")
    put("gross", "grossMargins",     1, 100)
    put("op",    "operatingMargins", 1, 100)
    put("net",   "profitMargins",    1, 100)

    fcf = info.get("freeCashflow")
    if fcf and mcap:
        try:
            v["pfcf"] = round(mcap / float(fcf), 2)
        except Exception:
            pass

    # 예상 매출 기준 PSR — 컨센서스가 있을 때만
    try:
        rev = next((e.get("rev") for e in (est or []) if e.get("rev")), None)
        if rev and mcap:
            v["fpsr"] = round(mcap / (rev * 1e9), 2)
    except Exception:
        pass
    return v


def fetch_detail(tk, info, hist, price, fwd_per, per3y, ma200):
    d = {
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "per3y": per3y,
        "ma200": round(ma200, 2) if ma200 else None,
    }

    for src, dst, mul in (("returnOnAssets", "roa", 100),
                          ("returnOnEquity", "roe", 100),
                          ("operatingMargins", "opm", 100),
                          ("profitMargins", "npm", 100),
                          ("targetMeanPrice", "target", 1)):
        v = info.get(src)
        if v is not None:
            try:
                d[dst] = round(float(v) * mul, 2 if mul == 1 else 1)
            except Exception:
                pass
    if d.get("target") and price:
        d["targetGap"] = pct(d["target"], price)
    if info.get("numberOfAnalystOpinions"):
        d["targetN"] = int(info["numberOfAnalystOpinions"])

    # 최근 1년 주가 — 주봉으로 줄여 담습니다. 일봉이면 파일이 다섯 배가 됩니다.
    try:
        close = hist["Close"].tail(260)
        wk = close.resample("W-FRI").last().dropna()
        d["px"] = [round(float(v), 2) for v in wk.tolist()][-53:]
        d["pxFrom"] = str(wk.index[-53:][0].date())
        d["hi52"] = round(float(close.max()), 2)
        d["lo52"] = round(float(close.min()), 2)
    except Exception:
        pass

    try:
        d["q"] = _statement(tk.quarterly_income_stmt, True,
                            {"rev": ("Total Revenue", "Revenue"),
                             "ni": ("Net Income", "Net Income Common Stockholders")}, 8)
    except Exception:
        d["q"] = []
    try:
        d["y"] = _statement(tk.income_stmt, False,
                            {"rev": ("Total Revenue", "Revenue"),
                             "op": ("Operating Income", "EBIT"),
                             "ni": ("Net Income", "Net Income Common Stockholders")}, 4)
    except Exception:
        d["y"] = []
    try:
        d["cf"] = _statement(tk.cashflow, False,
                             {"ocf": ("Operating Cash Flow",),
                              "capex": ("Capital Expenditure",),
                              "fcf": ("Free Cash Flow",)}, 4)
    except Exception:
        d["cf"] = []

    d["est"] = _estimates(tk, price, fwd_per)
    return {k: v for k, v in d.items() if v not in (None, [], {})}


def fetch_one(ticker: str, theme: str, hist_period: str = "4y"):
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    hist = tk.history(period=hist_period, auto_adjust=False)

    close = hist["Close"] if not hist.empty else None
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price is None and close is not None and len(close):
        price = float(close.iloc[-1])

    # 52주 고가
    high52 = info.get("fiftyTwoWeekHigh")
    if high52 is None and close is not None and len(close):
        high52 = float(close.tail(252).max())

    # 200일 이동평균
    ma200 = info.get("twoHundredDayAverage")
    if ma200 is None and close is not None and len(close) >= 200:
        ma200 = float(close.tail(200).mean())

    fwd_per = info.get("forwardPE")
    trailing_eps = info.get("trailingEps")
    forward_eps = info.get("forwardEps")

    # EPS 성장률: 선행 EPS 대비 후행 EPS. 없으면 야후 제공 성장률로 대체
    eps_growth = None
    if trailing_eps and forward_eps and trailing_eps > 0:
        eps_growth = round((forward_eps / trailing_eps - 1) * 100, 1)
    elif info.get("earningsGrowth") is not None:
        eps_growth = round(info["earningsGrowth"] * 100, 1)

    # 부채비율: 야후는 % 로 주므로 배수로 환산 (33.6 → 0.34)
    dte = info.get("debtToEquity")
    debt = round(dte / 100, 2) if dte is not None else None

    mcap = info.get("marketCap")
    mcap_b = round(mcap / 1e9, 1) if mcap else None

    per3y = PER3Y_OVERRIDE.get(ticker) or per3y_from_history(tk, hist)
    detail = fetch_detail(tk, info, hist, price, fwd_per, per3y, ma200)

    return {
        "종목": ticker,
        "테마": theme or info.get("sector") or "",
        "현재 주가": round(price, 2) if price else None,
        "고점대비 하락": pct(price, high52),
        "FWD PER": round(fwd_per, 2) if fwd_per else None,
        "3Y PER 괴리": pct(fwd_per, per3y) if (fwd_per and fwd_per > 0 and per3y) else None,
        "200일선 이격": pct(price, ma200),
        "시가총액": mcap_b,
        "EPS 성장": eps_growth,
        "RSI": rsi_wilder(close),
        "부채비율": debt,
        "_3Y평균PER(근사)": per3y,
        "_detail": detail,
        "_valu": fetch_valuation(info, hist, price, mcap, detail.get("est")),
    }


def fetch_market():
    """시장 타이밍 3지표 중 자동으로 구할 수 있는 두 개."""
    out = {}
    try:
        spy = yf.Ticker("SPY")
        h = spy.history(period="1y")["Close"]
        out["SPY 52주 고점대비"] = round((float(h.iloc[-1]) / float(h.max()) - 1) * 100, 1)
    except Exception:
        out["SPY 52주 고점대비"] = None
    try:
        vix = yf.Ticker("^VIX").history(period="5d")["Close"]
        out["VIX"] = round(float(vix.iloc[-1]), 2)
    except Exception:
        out["VIX"] = None

    # 상세 탭의 시장 환경 표에만 쓰이는 지수들
    idx = {}
    for sym, key in (("^IXIC", "NASDAQ"), ("^GSPC", "S&P500")):
        try:
            h = yf.Ticker(sym).history(period="1y")["Close"]
            now, hi = float(h.iloc[-1]), float(h.max())
            idx[key] = {"now": round(now, 1), "hi": round(hi, 1),
                        "dd": round((now / hi - 1) * 100, 1)}
        except Exception:
            pass
    out["_indices"] = idx
    return out


# ─────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────
COLUMNS = ["종목", "테마", "현재 주가", "고점대비 하락", "FWD PER", "3Y PER 괴리",
           "200일선 이격", "시가총액", "EPS 성장", "RSI", "부채비율"]


def load_watchlist(path):
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            keys = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            t = keys.get("ticker") or keys.get("종목") or keys.get("symbol")
            if t:
                out.append((t.upper(), keys.get("theme") or keys.get("테마") or ""))
    return out


def main():
    ap = argparse.ArgumentParser(description="미국주식 시그널 보드 데이터 수집")
    ap.add_argument("--tickers", help="쉼표로 구분한 종목 코드. 예: NVDA,AAPL,MSFT")
    ap.add_argument("--watchlist", help="ticker,theme 두 열짜리 CSV 경로")
    ap.add_argument("--out", default=None, help="저장할 CSV 파일명")
    ap.add_argument("--delay", type=float, default=0.4, help="종목별 대기 초 (기본 0.4)")
    args = ap.parse_args()

    if args.watchlist:
        watch = load_watchlist(args.watchlist)
    elif args.tickers:
        watch = [(t.strip().upper(), "") for t in args.tickers.split(",") if t.strip()]
    else:
        watch = DEFAULT_WATCHLIST

    out_path = args.out or f"signal_data_{datetime.now():%Y%m%d}.csv"

    rows, failed = [], []
    print(f"{len(watch)}개 종목을 수집합니다.\n")
    for i, (t, theme) in enumerate(watch, 1):
        try:
            r = fetch_one(t, theme)
            rows.append(r)
            print(f"  [{i:>2}/{len(watch)}] {t:<6} "
                  f"${r['현재 주가']}  고점대비 {r['고점대비 하락']}%  "
                  f"PER {r['FWD PER']}  RSI {r['RSI']}")
        except Exception as e:
            failed.append((t, str(e)[:70]))
            print(f"  [{i:>2}/{len(watch)}] {t:<6} 실패 — {str(e)[:70]}")
        time.sleep(args.delay)

    if not rows:
        sys.exit("\n수집된 종목이 없습니다. 종목 코드와 인터넷 연결을 확인하세요.")

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    mkt = fetch_market()
    print(f"\n저장했습니다 → {out_path}  ({len(rows)}개 종목)")
    if failed:
        print(f"실패 {len(failed)}개: " + ", ".join(t for t, _ in failed))

    print("\n시장 타이밍 — 보드 오른쪽 칸에 직접 입력하세요")
    print(f"  SPY 52주 고점대비 : {mkt['SPY 52주 고점대비']}%")
    print(f"  VIX              : {mkt['VIX']}")
    print("  F&G INDEX        : cnn.com/markets/fear-and-greed 에서 확인 후 입력")

    print("\n다음 단계")
    print(f"  1. {out_path} 를 엑셀이나 구글시트로 열기")
    print("  2. 헤더 행까지 포함해 전체 복사")
    print("  3. signal_board.html 의 [표 붙여넣기] → 붙여넣기 → 불러오기")

    blank = [c for c in COLUMNS if sum(1 for r in rows if r.get(c) is None) > len(rows) * 0.3]
    if blank:
        print(f"\n참고: 다음 항목은 결측이 많습니다 → {', '.join(blank)}")
        print("      보드에서 결측은 0점 처리되니, 중요한 값은 직접 채워 넣으세요.")


if __name__ == "__main__":
    main()
