#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py — 미국 52주 신고가 데일리 파이프라인 (웹사이트용)
=============================================================
Finviz 스크리너(s=ta_newhigh, geo_usa, 시총 $2B 이상)에서 신고가 종목을 수집해
섹터 breadth / 산업 핫스팟 / 주도주를 분석하고, 결과를 docs/data.json으로 저장합니다.
GitHub Actions가 미국 장마감 후 자동 실행 → GitHub Pages 대시보드가 갱신됩니다.

로컬 수동 실행:
  pip install -r requirements.txt
  python pipeline.py                # 기본: 시총 $2B 이상
  python pipeline.py --min-cap 10   # $10B 이상
  python pipeline.py --no-llm       # Claude 정성분석 생략
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from finvizfinance.screener.overview import Overview as ScreenerOverview
    from finvizfinance.group.overview import Overview as GroupOverview
except ImportError:
    sys.exit("finvizfinance가 필요합니다: pip install -r requirements.txt")

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "docs"
DOCS_DIR.mkdir(exist_ok=True)

KST = timezone(timedelta(hours=9))
ET_OFFSET_NOTE = "미국 동부시간 기준 장마감(16:00 ET) 이후 자동 수집"

SECTOR_KR = {
    "Technology": "IT/기술",
    "Financial": "금융",
    "Healthcare": "헬스케어",
    "Consumer Cyclical": "경기소비재",
    "Communication Services": "커뮤니케이션",
    "Industrials": "산업재",
    "Consumer Defensive": "필수소비재",
    "Energy": "에너지",
    "Utilities": "유틸리티",
    "Basic Materials": "소재",
    "Real Estate": "부동산/리츠",
}

CAP_FILTER = {
    2.0: "+Mid (over $2bln)",
    10.0: "+Large (over $10bln)",
    200.0: "Mega ($200bln and more)",
}


# ----------------------------------------------------------------------------
# 수집
# ----------------------------------------------------------------------------
def fetch_new_highs(min_cap: float) -> pd.DataFrame:
    key = min(CAP_FILTER, key=lambda k: abs(k - min_cap))
    filters = {"Country": "USA", "Market Cap.": CAP_FILTER[key]}
    print(f"[1/5] Finviz 신고가 수집 (필터: {CAP_FILTER[key]}, 요청 간 1초 대기)...")

    scr = ScreenerOverview()
    scr.set_filter(signal="New High", filters_dict=filters)
    df = scr.screener_view(order="Market Cap.", ascend=False, sleep_sec=1, verbose=0)
    if df is None or df.empty:
        print("    신고가 종목 없음 (약세장 또는 휴장일) — 빈 결과로 진행")
        return pd.DataFrame(columns=["Ticker", "Company", "Sector", "Industry",
                                     "mktcap", "P/E", "Change"])
    df = df.rename(columns={"Market Cap": "mktcap"})
    df["mktcap"] = pd.to_numeric(df["mktcap"], errors="coerce")
    print(f"    신고가 {len(df)}개 수집")
    return df


def fetch_sector_totals() -> pd.Series | None:
    try:
        g = GroupOverview().screener_view(group="Sector")
        col = next((c for c in g.columns if c.lower() in ("stocks", "companies")), None)
        return g.set_index("Name")[col].astype(int) if col else None
    except Exception as e:
        print(f"    그룹 통계 실패({e}) — 비율 생략")
        return None


# ----------------------------------------------------------------------------
# 분석
# ----------------------------------------------------------------------------
def sector_analysis(df: pd.DataFrame, totals: pd.Series | None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby("Sector")
    out = pd.DataFrame({
        "count": g.size(),
        "cap_sum_b": (g["mktcap"].sum() / 1e9).round(1),
        "cap_med_b": (g["mktcap"].median() / 1e9).round(2),
    })
    if totals is not None:
        out["total"] = out.index.map(totals)
        out["ratio"] = (out["count"] / out["total"] * 100).round(1)
        out = out.sort_values("ratio", ascending=False)
    else:
        out["total"] = np.nan
        out["ratio"] = np.nan
        out = out.sort_values("count", ascending=False)
    out["sector_kr"] = out.index.map(lambda s: SECTOR_KR.get(s, s))
    return out


def industry_hotspots(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(["Sector", "Industry"]).agg(
        count=("Ticker", "size"),
        cap_b=("mktcap", lambda x: round(x.sum() / 1e9, 1)),
        tickers=("Ticker", lambda x: ", ".join(
            df.loc[x.index].nlargest(3, "mktcap")["Ticker"])),
    ).reset_index()
    return g.nlargest(n, "count")


def find_leaders(df: pd.DataFrame, top_sectors: list, n: int = 5) -> pd.DataFrame:
    rows = []
    for sec in top_sectors:
        sub = df[df["Sector"] == sec].nlargest(n, "mktcap")
        for _, r in sub.iterrows():
            rows.append({
                "sector": sec, "sector_kr": SECTOR_KR.get(sec, sec),
                "ticker": r["Ticker"], "name": r["Company"],
                "industry": r["Industry"],
                "cap_b": round(r["mktcap"] / 1e9, 1) if pd.notna(r["mktcap"]) else None,
                "pe": None if pd.isna(pd.to_numeric(r.get("P/E"), errors="coerce"))
                      else float(pd.to_numeric(r.get("P/E"), errors="coerce")),
                "change": str(r.get("Change", "")),
            })
    return pd.DataFrame(rows)


def enrich_momentum(leaders: pd.DataFrame) -> pd.DataFrame:
    """주도주 3M 수익률 보강 (소량, 실패해도 무시)"""
    if leaders.empty:
        return leaders
    try:
        import yfinance as yf
        px = yf.download(leaders["ticker"].tolist(), period="6mo",
                         auto_adjust=True, progress=False)["Close"]
        if isinstance(px, pd.Series):
            px = px.to_frame(leaders["ticker"].iloc[0])
        ret3m = ((px.iloc[-1] / px.iloc[max(0, len(px) - 64)] - 1) * 100).round(1)
        leaders["ret_3m"] = leaders["ticker"].map(ret3m)
    except Exception as e:
        print(f"    모멘텀 보강 생략({e})")
        leaders["ret_3m"] = None
    return leaders


# ----------------------------------------------------------------------------
# 정성분석 (Claude API / 룰베이스)
# ----------------------------------------------------------------------------
LLM_SYSTEM = """당신은 기관투자자용 미국 주식 전략 애널리스트입니다.
Finviz 52주 신고가 데이터(시총 $2B 이상)를 근거로 한국어 존댓말 마크다운으로 작성하세요.
데이터에 없는 수치를 만들지 마세요. 웹 대시보드에 그대로 게시되므로 간결하게 쓰세요.

## 왜 이 섹터들이 신고가를 갔는가
- 산업 핫스팟 구성과 뉴스에서 추론 가능한 드라이버 (매크로 vs 산업 vs 개별기업 구분)

## 투자자가 알아야 하는 사항
- breadth 협소도, 산업 집중도, 모멘텀 과열, 신고가 매매의 통계적 특성
- 데이터에서 확인되는 경고 신호를 수치와 함께

## Key Insights
- 3~5개 bullet, 각 한 문장 판단 + 근거 수치"""


def fetch_news(tickers: list, per: int = 4) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        return {}
    news = {}
    for t in tickers[:10]:
        try:
            items = yf.Ticker(t).news or []
            news[t] = [it.get("content", it).get("title", "") for it in items[:per]]
        except Exception:
            news[t] = []
    return news


def llm_analysis(breadth, hotspots, leaders, news) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    payload = json.dumps({
        "기준일": datetime.now(KST).strftime("%Y-%m-%d"),
        "섹터분포": breadth.reset_index().to_dict(orient="records"),
        "산업핫스팟": hotspots.to_dict(orient="records"),
        "주도주": leaders.to_dict(orient="records"),
        "뉴스": news,
    }, ensure_ascii=False, default=str)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=2500,
            system=LLM_SYSTEM,
            messages=[{"role": "user", "content": payload}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")
    except Exception as e:
        print(f"    LLM 분석 실패({e}) — 룰베이스로 대체")
        return None


def rule_based(breadth: pd.DataFrame, hotspots: pd.DataFrame,
               df: pd.DataFrame) -> str:
    if df.empty:
        return "## 오늘의 시그널\n\n신고가 종목이 없습니다. 시장 breadth가 극도로 위축된 상태입니다."
    lines = ["## 오늘의 정량 시그널\n"]
    for sec, r in breadth.head(3).iterrows():
        ratio = f" (섹터 내 {r['ratio']}%)" if pd.notna(r.get("ratio")) else ""
        lines.append(f"- **{r['sector_kr']}**: 신고가 {int(r['count'])}개{ratio}, "
                     f"시총합 ${r['cap_sum_b']}B")
    med = df["mktcap"].median() / 1e9
    lines.append(f"\n**신고가 종목 중앙값 시총 ${med:.1f}B** — "
                 + ("중대형주 동반 랠리로 질적으로 양호합니다."
                    if med >= 5 else "시총 하단 종목 비중이 높아 유동성에 유의하십시오."))
    if not hotspots.empty:
        h = hotspots.iloc[0]
        lines.append(f"\n**최다 산업 핫스팟**: {h['Industry']} "
                     f"({int(h['count'])}개 · 대표 {h['tickers']})")
    lines.append("\n> Claude API 키를 설정하면 뉴스 기반 원인 분석이 자동 추가됩니다.")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# 출력: docs/data.json (+ 히스토리 누적)
# ----------------------------------------------------------------------------
def write_outputs(df, breadth, hotspots, leaders, insight_md, min_cap):
    now_kst = datetime.now(KST)
    data = {
        "as_of": now_kst.strftime("%Y-%m-%d"),
        "updated_at": now_kst.strftime("%Y-%m-%d %H:%M KST"),
        "note": ET_OFFSET_NOTE,
        "min_cap_b": min_cap,
        "total_count": int(len(df)),
        "median_cap_b": round(float(df["mktcap"].median() / 1e9), 2) if len(df) else 0,
        "tape": df.nlargest(min(60, len(df)), "mktcap")[["Ticker", "Change"]]
                  .rename(columns={"Ticker": "t", "Change": "c"})
                  .to_dict(orient="records") if len(df) else [],
        "sectors": breadth.reset_index().rename(columns={"index": "sector", "Sector": "sector"})
                          .replace({np.nan: None}).to_dict(orient="records")
                   if not breadth.empty else [],
        "hotspots": hotspots.replace({np.nan: None}).to_dict(orient="records")
                    if not hotspots.empty else [],
        "leaders": leaders.replace({np.nan: None}).to_dict(orient="records")
                   if not leaders.empty else [],
        "insight_md": insight_md,
    }
    out = DOCS_DIR / "data.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[5/5] 저장: {out}")

    # breadth 히스토리 누적 (시계열 다이버전스 분석용)
    hist_path = DOCS_DIR / "history.csv"
    row = {"date": data["as_of"], "total": data["total_count"],
           "median_cap_b": data["median_cap_b"]}
    for s in data["sectors"]:
        row[s["sector"]] = s["count"]
    hist = pd.DataFrame([row])
    if hist_path.exists():
        prev = pd.read_csv(hist_path)
        prev = prev[prev["date"] != data["as_of"]]  # 같은 날 재실행 시 덮어쓰기
        hist = pd.concat([prev, hist], ignore_index=True)
    hist.to_csv(hist_path, index=False, encoding="utf-8-sig")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-cap", type=float, default=2.0,
                    help="최소 시총 $B (기본 2.0 — 소형주 노이즈 제거)")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    df = fetch_new_highs(args.min_cap)

    print("[2/5] 섹터/산업 분석...")
    totals = fetch_sector_totals()
    breadth = sector_analysis(df, totals)
    hotspots = industry_hotspots(df)

    print("[3/5] 주도주 판별...")
    top_sectors = breadth.head(3).index.tolist() if not breadth.empty else []
    leaders = enrich_momentum(find_leaders(df, top_sectors))

    print("[4/5] 정성분석...")
    insight = None
    if not args.no_llm and not leaders.empty:
        news = fetch_news(leaders["ticker"].tolist())
        insight = llm_analysis(breadth, hotspots, leaders, news)
    if insight is None:
        insight = rule_based(breadth, hotspots, df)

    write_outputs(df, breadth, hotspots, leaders, insight, args.min_cap)
    print("완료. docs/index.html을 열면 대시보드를 확인할 수 있습니다.")


if __name__ == "__main__":
    main()
