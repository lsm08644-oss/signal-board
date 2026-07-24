#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_board.py — 데이터까지 박힌 시그널 보드를 통째로 만들어 냅니다.

signal_board.html 을 틀로 삼아, 주가를 새로 받아 안에 심은 board.html 을 씁니다.
붙여넣기 단계가 사라지므로, 이 파일 하나만 스케줄러에 걸면 매일 자동 갱신됩니다.

설치:  pip install yfinance pandas
실행:  python build_board.py
       python build_board.py --open              (다 만들고 브라우저로 바로 열기)
       python build_board.py --watchlist my.csv
       python build_board.py --out C:/보드/board.html

같은 폴더에 signal_board.html 이 있어야 합니다.
"""

import argparse
import json
import os
import sys
import time
import webbrowser
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from fetch_signals import (DEFAULT_WATCHLIST, fetch_one, fetch_market,
                               load_watchlist)
except ImportError:
    sys.exit("같은 폴더에 fetch_signals.py 가 있어야 합니다.")

MARKER = "<!--BOARD_DATA-->"


def fear_and_greed():
    """CNN 공포·탐욕 지수. 비공식 경로라 막히면 조용히 None 을 돌려줍니다."""
    try:
        import requests
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=10,
        )
        r.raise_for_status()
        return round(float(r.json()["fear_and_greed"]["score"]), 0)
    except Exception:
        return None


# CSV 헤더 이름 → 보드 내부 키
FIELD_MAP = {
    "종목": "t", "테마": "theme", "현재 주가": "price", "고점대비 하락": "dd",
    "FWD PER": "fwdPer", "3Y PER 괴리": "perGap", "200일선 이격": "maGap",
    "시가총액": "mcap", "EPS 성장": "eps", "RSI": "rsi", "부채비율": "debt",
}


def to_board_row(rec):
    out = {}
    for src, key in FIELD_MAP.items():
        v = rec.get(src)
        out[key] = v if v is not None else None
    return out


def main():
    ap = argparse.ArgumentParser(description="시그널 보드 생성")
    ap.add_argument("--watchlist", help="ticker,theme CSV 경로")
    ap.add_argument("--tickers", help="쉼표 구분 종목 코드")
    ap.add_argument("--template", default=None, help="틀로 쓸 signal_board.html 경로")
    ap.add_argument("--out", default=None, help="만들어 낼 파일 경로 (기본 board.html)")
    ap.add_argument("--fng", type=float, default=None,
                    help="공포탐욕지수를 직접 지정 (자동 수집 실패 대비)")
    ap.add_argument("--open", action="store_true", help="완성 후 브라우저로 열기")
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    template = args.template or os.path.join(here, "signal_board.html")
    out_path = args.out or os.path.join(here, "board.html")

    if not os.path.exists(template):
        sys.exit(f"틀 파일이 없습니다: {template}")
    html = open(template, encoding="utf-8").read()
    if MARKER not in html:
        sys.exit(f"틀 파일에서 {MARKER} 를 찾지 못했습니다. 원본 signal_board.html 을 쓰세요.")

    if args.watchlist:
        watch = load_watchlist(args.watchlist)
    elif args.tickers:
        watch = [(t.strip().upper(), "") for t in args.tickers.split(",") if t.strip()]
    else:
        watch = DEFAULT_WATCHLIST

    rows, failed = [], []
    print(f"{len(watch)}개 종목 수집 중\n")
    for i, (t, theme) in enumerate(watch, 1):
        try:
            rec = fetch_one(t, theme)
            rows.append(to_board_row(rec))
            print(f"  [{i:>2}/{len(watch)}] {t:<6} ${rec['현재 주가']}  "
                  f"고점대비 {rec['고점대비 하락']}%")
        except Exception as e:
            failed.append(t)
            print(f"  [{i:>2}/{len(watch)}] {t:<6} 실패 — {str(e)[:60]}")
        time.sleep(args.delay)

    if not rows:
        sys.exit("\n수집된 종목이 없습니다. 인터넷 연결과 종목 코드를 확인하세요.")

    mkt = fetch_market()
    fng = args.fng if args.fng is not None else fear_and_greed()

    # 지난번 성공값을 옆에 저장해 두고, 이번에 못 받은 항목만 그걸로 메웁니다.
    # 무인 실행이라 실패를 눈으로 못 보니, 엉뚱한 기본값이 진짜처럼 보이는 걸 막는 장치입니다.
    last_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "last_market.json")
    last = {}
    try:
        with open(last_path, encoding="utf-8") as f:
            last = json.load(f)
    except Exception:
        pass

    market, carried = {}, []
    for key, fresh in (("spy", mkt.get("SPY 52주 고점대비")),
                       ("vix", mkt.get("VIX")),
                       ("fng", fng)):
        if fresh is not None:
            market[key] = fresh
        elif last.get(key) is not None:
            market[key] = last[key]
            carried.append(f"{key}={last[key]} ({last.get('date','이전값')})")
        else:
            market[key] = None

    now = datetime.now()
    payload = {
        "rows": rows,
        "market": market,
        "asOf": now.strftime("%m.%d %H:%M"),
        "asOfISO": now.isoformat(timespec="seconds"),
    }

    inject = (MARKER + "\n<script>window.__BOARD_DATA__ = "
              + json.dumps(payload, ensure_ascii=False) + ";</script>")
    open(out_path, "w", encoding="utf-8").write(html.replace(MARKER, inject, 1))

    try:
        with open(last_path, "w", encoding="utf-8") as f:
            json.dump(dict(market, date=now.strftime("%Y-%m-%d")), f, ensure_ascii=False)
    except Exception:
        pass

    print(f"\n완성 → {out_path}   ({len(rows)}개 종목)")
    if failed:
        print(f"실패 {len(failed)}개: {', '.join(failed)}")
    print(f"시장 타이밍  SPY {market['spy']}%  VIX {market['vix']}  F&G {market['fng']}")
    if carried:
        print(f"  ※ 오늘 못 받아 이전 값을 그대로 쓴 항목: {', '.join(carried)}")
    if market["fng"] is None:
        print("  ※ 공포탐욕지수가 비어 있습니다. 보드에서 직접 넣거나 --fng 40 처럼 넘기세요.")

    # 절반 넘게 실패하면 실패로 끝냅니다. 깃허브가 커밋을 건너뛰어
    # 어제의 멀쩡한 보드가 깨진 보드로 덮이지 않게 하려는 겁니다.
    if len(rows) < max(1, len(watch) // 2):
        print(f"\n수집 성공이 {len(rows)}/{len(watch)} 뿐이라 실패로 처리합니다. "
              f"기존 보드는 그대로 둡니다.")
        sys.exit(1)

    if args.open:
        webbrowser.open("file://" + os.path.abspath(out_path))


if __name__ == "__main__":
    main()
