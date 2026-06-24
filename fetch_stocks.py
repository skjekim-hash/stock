#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
고도화된 국내 주식 데이터 수집 + 분석 스크립트
"""

import json, sys, re, time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlencode, quote

PYKRX_AVAILABLE = False

import os
KIS_APP_KEY    = os.environ.get("KIS_APP_KEY", "")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET", "")
DART_API_KEY   = os.environ.get("DART_API_KEY", "")
KIS_AVAILABLE  = bool(KIS_APP_KEY and KIS_APP_SECRET)
KIS_BASE_URL   = ("https://openapi.koreainvestment.com:9443"
                  if os.environ.get("KIS_REAL", "").lower() in ("1", "true", "yes")
                  else "https://openapivts.koreainvestment.com:29443")
KIS_TOKEN      = {"access_token": "", "expires": 0}

STOCKS = [
    {"code": "000660", "yf": "000660.KS", "name": "SK하이닉스", "emoji": "🔵"},
    {"code": "005930", "yf": "005930.KS", "name": "삼성전자",   "emoji": "🟡"},
    {"code": "066570", "yf": "066570.KS", "name": "LG전자",     "emoji": "🔴"},
    {"code": "009150", "yf": "009150.KS", "name": "삼성전기",   "emoji": "🟠"},
    {"code": "005380", "yf": "005380.KS", "name": "현대자동차", "emoji": "🟢"},
    {"code": "105560", "yf": "105560.KS", "name": "KB금융",     "emoji": "🟣"},
    {"code": "017670", "yf": "017670.KS", "name": "SK텔레콤",   "emoji": "🩵"},
    {"code": "035420", "yf": "035420.KS", "name": "NAVER",      "emoji": "🟤"},
]
KOSPI_CODE = "0001"
KST = timezone(timedelta(hours=9))


def http_get(url, timeout=8, headers=None):
    h = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if headers: h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")

def http_json(url, timeout=15, headers=None):
    return json.loads(http_get(url, timeout, headers))

def safe(fn, default=None):
    try: return fn()
    except Exception as e:
        print(f"  ⚠ {fn.__name__ if hasattr(fn,'__name__') else '?'}: {e}", file=sys.stderr)
        return default

def to_n(v, default=0):
    if v is None: return default
    try: return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError): return default


# ─── KIS API ───────────────────────────────────────────────────────────────
def kis_get_token():
    global KIS_TOKEN
    if not KIS_AVAILABLE:
        print("  KIS 비활성 — Secrets 미설정", file=sys.stderr)
        return ""
    if KIS_TOKEN["access_token"] and time.time() < KIS_TOKEN["expires"]:
        return KIS_TOKEN["access_token"]
    try:
        url = f"{KIS_BASE_URL}/oauth2/tokenP"
        body = json.dumps({"grant_type": "client_credentials",
                           "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET}).encode()
        req = Request(url, data=body, method="POST",
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        token = d.get("access_token", "")
        if token:
            KIS_TOKEN["access_token"] = token
            KIS_TOKEN["expires"] = time.time() + 3600 * 23
            return token
    except Exception as e:
        print(f"  KIS 토큰 실패: {e}", file=sys.stderr)
    return ""

def kis_request(path, params, tr_id):
    token = kis_get_token()
    if not token: return None
    url = f"{KIS_BASE_URL}{path}?{urlencode(params)}"
    req = Request(url, headers={
        "authorization": f"Bearer {token}", "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET, "tr_id": tr_id,
        "custtype": "P", "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  KIS 요청 실패 ({tr_id}): {e}", file=sys.stderr)
    return None

def fetch_kis_price(code):
    if not KIS_AVAILABLE: return None
    try:
        d = kis_request("/uapi/domestic-stock/v1/quotations/inquire-price",
                        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
                        "FHKST01010100")
        if not d: return None
        out = d.get("output", {})
        price = to_n(out.get("stck_prpr") or 0)
        prev  = to_n(out.get("stck_sdpr") or 0)
        if price > 0:
            high52 = to_n(out.get("w52_hgpr") or 0)
            low52  = to_n(out.get("w52_lwpr") or 0)
            change = to_n(out.get("prdy_vrss") or 0)
            sign   = out.get("prdy_vrss_sign") or "3"
            if sign in ("4", "5"): change = -abs(change)
            return {
                "price": round(price), "prevClose": round(prev) if prev else round(price - change),
                "change": round(change),
                "changePct": round(to_n(out.get("prdy_ctrt") or 0), 2) * (-1 if sign in ("4","5") else 1),
                "high52w": round(high52), "low52w": round(low52),
                "tradedAt": out.get("stck_bsop_date", ""), "source": "KIS API (통합시세)",
                "creditRatio": to_n(out.get("crdt_rsrs_rt") or 0),   # 신용잔고율
                "marginRate": to_n(out.get("marg_rate") or 0),        # 증거금 비율
            }
    except Exception as e:
        print(f"  KIS 주가 실패 ({code}): {e}", file=sys.stderr)
    return None

def fetch_kis_investor(code):
    if not KIS_AVAILABLE: return None
    if "openapivts" in KIS_BASE_URL: return None
    try:
        d = kis_request("/uapi/domestic-stock/v1/quotations/inquire-investor",
                        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
                        "FHKST01010900")
        if not d: return None
        out = d.get("output", {})
        f    = round(to_n(out.get("frgn_ntby_qty") or out.get("frgn_seln_vol") or 0))
        inst = round(to_n(out.get("orgn_ntby_qty") or out.get("inst_ntby_vol") or 0))
        indv = round(to_n(out.get("indvdl_ntby_qty") or 0))
        if f > 0 and inst > 0:
            trend, comment = "매수우세", f"외국인 +{f:,}주 · 기관 +{inst:,}주 동반 순매수"
        elif f > 0:
            trend, comment = "매수우세", f"외국인 +{f:,}주 순매수"
        elif f < 0 and inst < 0:
            trend, comment = "매도우세", f"외국인 {f:,}주 · 기관 {inst:,}주 동반 순매도"
        elif f < 0:
            trend, comment = "매도우세", f"외국인 {f:,}주 순매도"
        else:
            trend, comment = "중립", "외국인·기관 수급 중립"
        return {"foreign": f, "institution": inst, "individual": indv,
                "foreignTrend": trend, "comment": comment, "date": "당일"}
    except Exception as e:
        print(f"  KIS 수급 실패 ({code}): {e}", file=sys.stderr)
    return None

def fetch_kis_short(code):
    if not KIS_AVAILABLE: return None
    if "openapivts" in KIS_BASE_URL: return None
    try:
        d = kis_request(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
             "FID_INPUT_DATE_1": datetime.now(KST).strftime("%Y%m%d"),
             "FID_INPUT_DATE_2": datetime.now(KST).strftime("%Y%m%d"),
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
            "FHKST03010100")
        if d:
            out = d.get("output2", [{}])
            if out:
                ratio = to_n(out[0].get("short_sell_rate") or 0)
                if ratio > 0:
                    comment = ("공매도 비율 높음" if ratio > 5 else
                               "공매도 비율 보통" if ratio > 2 else "공매도 비율 낮음")
                    return {"ratio": round(ratio, 2), "volume": 0, "comment": comment}
    except Exception as e:
        print(f"  KIS 공매도 실패 ({code}): {e}", file=sys.stderr)
    return None


# ─── 네이버 금융 ───────────────────────────────────────────────────────────
def fetch_nxt_prices():
    """NXT(애프터마켓) 실시간 가격을 8종목 한 번에 수집.
    네이버 front-api/realTime/marketPrice의 overMarketPriceInfo.overPrice 사용.
    NXT 거래중(OPEN)일 때만 유효. 정규장/마감 시간엔 빈 dict 반환."""
    codes = ",".join(s["code"] for s in STOCKS)
    url = (f"https://m.stock.naver.com/front-api/realTime/marketPrice"
           f"?itemCodes={codes}&endType=stock&stockType=domestic")
    out = {}
    try:
        d = http_json(url)
        datas = (d.get("result") or {}).get("datas") or []
        for item in datas:
            code = item.get("itemCode")
            om = item.get("overMarketPriceInfo") or {}
            status = om.get("overMarketStatus")  # OPEN / CLOSE
            over_price = to_n(om.get("overPrice"))
            close_price = to_n(item.get("closePrice"))
            if code and over_price > 0:
                # 정규장 종가 대비 NXT 등락
                nxt_chg = round((over_price - close_price) / close_price * 100, 2) if close_price > 0 else 0
                out[code] = {
                    "nxtPrice": round(over_price),
                    "closePrice": round(close_price),
                    "nxtChgPct": nxt_chg,
                    "status": status,  # OPEN이면 거래중
                    "session": om.get("tradingSessionType", ""),  # AFTER_MARKET / PRE_MARKET
                }
        print(f"  NXT 가격 수집: {len(out)}종목", file=sys.stderr)
    except Exception as e:
        print(f"  NXT 가격 수집 실패: {e}", file=sys.stderr)
    return out


def fetch_naver_price(code):
    try:
        d = http_json(f"https://m.stock.naver.com/api/stock/{code}/price?pageSize=2&page=1")
        rows = d if isinstance(d, list) else d.get("priceInfos") or d.get("prices") or []
        if rows:
            latest = rows[0]
            price = to_n(latest.get("closePrice") or latest.get("nv") or 0)
            traded_at = latest.get("localTradedAt") or latest.get("tradeTime") or ""
            if price > 0:
                prev = high52w = low52w = 0
                try:
                    b = http_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
                    chg = to_n(b.get("compareToPreviousClosePrice", 0))
                    prev = round(price - chg) if chg else round(price)
                    high52w = round(to_n(b.get("highPrice")) or to_n(b.get("yearHighPrice")))
                    low52w  = round(to_n(b.get("lowPrice"))  or to_n(b.get("yearLowPrice")))
                except: pass
                return {"price": round(price), "prevClose": prev,
                        "high52w": high52w, "low52w": low52w,
                        "tradedAt": str(traded_at)[:19], "source": "네이버 금융"}
    except Exception as e:
        print(f"  네이버 price 실패 ({code}): {e}", file=sys.stderr)
    try:
        d = http_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
        price = (to_n(d.get("closePrice")) or to_n(d.get("currentPrice"))
                 or to_n(d.get("nv")) or to_n(d.get("now")))
        if price > 0:
            change_val = to_n(d.get("compareToPreviousClosePrice", 0))
            return {
                "price": round(price),
                "prevClose": round(price - change_val) if change_val else round(price),
                "high52w": round(to_n(d.get("highPrice")) or to_n(d.get("yearHighPrice"))),
                "low52w":  round(to_n(d.get("lowPrice"))  or to_n(d.get("yearLowPrice"))),
                "tradedAt": str(d.get("localTradedAt") or d.get("dealTradeTime") or "")[:19],
                "source": "네이버 금융",
            }
    except Exception as e:
        print(f"  네이버 basic 실패 ({code}): {e}", file=sys.stderr)
    return None

def fetch_short_selling(code):
    try:
        html = http_get(f"https://finance.naver.com/item/main.naver?code={code}",
                        headers={"Referer": "https://finance.naver.com/"})
        m = re.search(r"공매도[^0-9]*(\d+\.?\d*)\s*%", html)
        if m:
            ratio = float(m.group(1))
            comment = ("공매도 비율 높음" if ratio > 5 else
                       "공매도 비율 보통" if ratio > 2 else "공매도 비율 낮음")
            return {"ratio": ratio, "volume": 0, "comment": comment}
    except Exception as e:
        print(f"  공매도 HTML 실패 ({code}): {e}", file=sys.stderr)
    return {"ratio": 0, "volume": 0, "comment": "공매도 데이터 없음"}

def fetch_kospi():
    try:
        d = http_json("https://m.stock.naver.com/api/index/KOSPI/basic")
        price  = to_n(d.get("closePrice") or d.get("indexValue") or 0)
        change = to_n(d.get("compareToPreviousClosePrice") or 0)
        pct    = to_n(d.get("fluctuationsRatio") or 0)
        return {"price": round(price, 2), "change": round(change, 2), "changePct": round(pct, 2)}
    except Exception as e:
        print(f"  KOSPI 실패: {e}", file=sys.stderr)
    return {"price": 0, "change": 0, "changePct": 0}


def fetch_market_signal():
    """미국 선행지표로 '오늘의 시장 분위기' 판단 (전일 밤 → 오늘 아침 갭 선행)
    - ^SOX  필라델피아 반도체지수: 반도체주(하이닉스·삼성전자) 직접 선행
    - NQ=F  나스닥100 선물: 기술주 전반 분위기
    - KRW=X 원/달러 환율: 외국인 수급 (원화 약세=환율↑=매도 경향)
    - ^VIX  공포지수: 시장 변동성·위험회피 심리 (급등=공포)
    종합 점수가 음(-)이면 매수 신호를 보수적으로 누르는 브레이크로 활용."""
    targets = [
        ("^SOX",  "필라델피아 반도체", "sox"),
        ("NQ=F",  "나스닥 선물",       "nasdaq"),
        ("KRW=X", "원/달러 환율",      "fx"),
        ("^VIX",  "공포지수(VIX)",     "vix"),
        ("^TNX",  "미국 10년물 금리",  "tnx"),
        ("DX-Y.NYB", "달러인덱스",     "dxy"),
        ("CNY=X", "위안/달러",         "cny"),
        ("TSM",   "TSMC",             "tsmc"),
    ]
    out = {}
    for sym, name, key in targets:
        enc = quote(sym, safe="")  # ^SOX, NQ=F 등 특수문자 인코딩
        for base in ["query1", "query2"]:
            try:
                d = http_json(f"https://{base}.finance.yahoo.com/v8/finance/chart/{enc}?interval=1d&range=1d", timeout=5)
                meta = d.get("chart", {}).get("result", [{}])[0].get("meta", {})
                cur  = meta.get("regularMarketPrice") or 0
                prev = meta.get("chartPreviousClose") or meta.get("previousClose") or 0
                if cur and prev:
                    pct = round((cur - prev) / prev * 100, 2)
                    out[key] = {"name": name, "price": round(cur, 2), "pct": pct}
                    break
            except Exception as e:
                print(f"  시장지표 실패 ({sym}/{base}): {e}", file=sys.stderr)

    # 종합 점수: SOX·나스닥 상승=우호(+), 환율·VIX 상승=비우호(-)
    # VIX는 일일 변동폭이 커서(±5~10%) 가중치를 작게(0.3) 둠
    score = 0.0
    if "sox"    in out: score += out["sox"]["pct"]    * 1.5
    if "nasdaq" in out: score += out["nasdaq"]["pct"] * 1.0
    if "fx"     in out: score -= out["fx"]["pct"]     * 1.0
    # VIX 변동률: 절대 수준에 따라 가중 차등 (VIX는 본질적으로 '레벨'이 핵심)
    # 안정권(25미만)에선 변동률이 커도 약하게 — 18→20 같은 변동에 과민반응 방지
    # 공포권(30+)에선 변동률도 강하게 — 진짜 위험은 놓치지 않음
    if "vix" in out:
        vix_lv = out.get("vix", {}).get("price", 0)
        if   vix_lv >= 30: vix_w = 0.5
        elif vix_lv >= 25: vix_w = 0.3
        else:              vix_w = 0.1
        score -= out["vix"]["pct"] * vix_w
    # 미국 10년물 금리 상승 = 성장주(반도체·플랫폼)에 부담 (-)
    if "tnx"    in out: score -= out["tnx"]["pct"]    * 0.5

    # 절대 수준 감점: 환율·VIX는 '레벨' 자체가 위험 신호 (변동률과 별개)
    # ── 시장 상황 따라 조정하세요 ─────────────────────
    FX_WARN,  FX_DANGER  = 1500, 1550   # 원/달러 경계 / 위험 (1500 아래 안정)
    VIX_FEAR, VIX_PANIC  = 30,   40     # VIX 공포 / 패닉
    # ────────────────────────────────────────────────
    fx_price  = out.get("fx",  {}).get("price", 0)
    vix_price = out.get("vix", {}).get("price", 0)
    if   fx_price >= FX_DANGER: score -= 2
    elif fx_price >= FX_WARN:   score -= 1
    if   vix_price >= VIX_PANIC: score -= 2
    elif vix_price >= VIX_FEAR:  score -= 1
    # 금리 절대 수준: 4.5%↑ 성장주 부담, 5.0%↑ 강한 부담
    tnx_price = out.get("tnx", {}).get("price", 0)
    if   tnx_price >= 5.0: score -= 2
    elif tnx_price >= 4.5: score -= 1

    if   score >=  1.5: mood, label = "favorable", "우호적"
    elif score <= -1.5: mood, label = "adverse",   "비우호적"
    else:               mood, label = "neutral",   "중립"

    # 행동 제안형 한두 줄 해설 (지표 조합에 따라 자동 선택)
    fx_pct   = out.get("fx",  {}).get("pct", 0)     # 환율 +면 원화 약세(악재)
    sox_pct  = out.get("sox", {}).get("pct", 0)
    vix_val  = out.get("vix", {}).get("price", 0)   # VIX 절대수준
    vix_pct  = out.get("vix", {}).get("pct", 0)
    fx_spike  = fx_pct >= 1.0                        # 원/달러 1%+ 급등
    vix_fear  = vix_val >= 30 or vix_pct >= 15       # 공포 국면
    if mood == "adverse":
        if vix_fear:
            advice = "공포지수 급등. 변동성이 큰 날이라 급반등·급락이 섞일 수 있어요. 무리한 진입보다 관망, 사더라도 아주 잘게 나누세요."
        elif fx_spike:
            advice = "미국 약세 + 원화 급락. 하락이 며칠 이어질 수 있어요. 분할매수는 평소보다 더 잘게, 첫 지지선에 다 담지 마세요."
        elif sox_pct <= -2:
            advice = "반도체 약세. 하이닉스·삼성전자 갭하락 가능. 추격 진입 자제, 지지선 확인 후 분할매수."
        else:
            advice = "미국 기술주 약세. 갭하락 가능성. 한 번에 사지 말고 나눠서 대응하세요."
    elif mood == "favorable":
        advice = "미국 기술주 강세. 갭상승 가능성. 추격매수는 신중히, 눌림목 기다리는 것도 방법이에요."
    else:
        advice = "미국 시장 보합. 평소 전략대로 지지선·신호 중심으로 대응하세요."

    out["summary"] = {"mood": mood, "label": label, "score": round(score, 1), "advice": advice}

    # 위안/원화 짝 읽기 (점수 미반영, 해석 가이드용)
    # 위안 약세 + 원화 약세 = 아시아 전반 자금이탈 / 원화만 약세 = 한국 고유 악재
    cny_pct = out.get("cny", {}).get("pct", 0)
    if "cny" in out and "fx" in out:
        cny_weak = cny_pct >= 0.3   # 위안 약세(위안/달러 상승)
        krw_weak = fx_pct >= 0.3    # 원화 약세
        if krw_weak and cny_weak:
            out["summary"]["cnyNote"] = "위안·원화 동반 약세 — 아시아 전반 자금 이탈 분위기 (한국만의 문제는 아님). 외국인 매도 흐름 주시."
        elif krw_weak and not cny_weak:
            out["summary"]["cnyNote"] = "위안은 안정인데 원화만 약세 — 한국 고유 악재 가능성. 외국인 이탈 신호일 수 있어 더 주의."
        elif not krw_weak and cny_weak:
            out["summary"]["cnyNote"] = "위안 약세지만 원화는 견조 — 한국이 상대적으로 버티는 중. 차별화 흐름 확인."

    # TSMC ADR 짝 읽기 (점수 미반영, 반도체 선행 가이드용)
    # TSMC는 파운드리 1위 — SOX 지수보다 삼성전자·하이닉스에 더 직결된 선행지표
    tsmc_pct = out.get("tsmc", {}).get("pct", 0)
    sox_pct_now = out.get("sox", {}).get("pct", 0)
    if "tsmc" in out:
        if tsmc_pct >= 2:
            out["summary"]["tsmcNote"] = f"TSMC +{tsmc_pct}% 강세 — 삼성전자·SK하이닉스 내일 긍정적. 단 추격보다 눌림 대기."
        elif tsmc_pct <= -2:
            out["summary"]["tsmcNote"] = f"TSMC {tsmc_pct}% 약세 — 삼성전자·SK하이닉스 갭하락 주의. 지지선 확인 후 분할."
        elif sox_pct_now <= -1 and tsmc_pct >= 0:
            out["summary"]["tsmcNote"] = f"SOX 약세지만 TSMC는 견조({'+' if tsmc_pct>=0 else ''}{tsmc_pct}%) — 반도체 차별화. 개별 종목 수급 확인."
        elif sox_pct_now >= 1 and tsmc_pct <= 0:
            out["summary"]["tsmcNote"] = f"SOX 강세지만 TSMC는 약세({tsmc_pct}%) — 파운드리 부진. 삼성·하이닉스 신중."
    # ── 다음날 방향+강도 예측 (참고용 — 정확한 예측 아님, 야간지표 경향) ──
    # 코스피 전체: 종합 score 기반. 반도체: SOX·TSMC 직결.
    def _dir_strength(val, strong, mid):
        a = abs(val)
        if a >= strong: lvl = "강"
        elif a >= mid:  lvl = "중"
        else:           lvl = "약"
        if val > 0.3:   return "상승", lvl, "#00c896"
        elif val < -0.3: return "하락", lvl, "#ff5c7a"
        else:           return "보합", "약", "#9ab"

    # 코스피 방향: 종합 score를 갭% 경향으로 환산 (보수적, score 1당 ~0.3%)
    kospi_bias = round(score * 0.3, 1)
    kdir, klvl, kcolor = _dir_strength(kospi_bias, 1.5, 0.6)
    # 반도체 방향: SOX 60% + TSMC 40% 가중 (반도체 직결 선행)
    semi_bias = round(sox_pct_now * 0.6 + tsmc_pct * 0.4, 1)
    sdir, slvl, scolor = _dir_strength(semi_bias, 1.5, 0.7)

    out["forecast"] = {
        "kospi": {
            "dir": kdir, "strength": klvl, "color": kcolor,
            "bias": kospi_bias,
            "low": round(kospi_bias - 0.5, 1), "high": round(kospi_bias + 0.5, 1),
            "text": f"{kdir} 경향 {klvl} (대략 {'+' if kospi_bias>=0 else ''}{kospi_bias}% 안팎)",
        },
        "semi": {
            "dir": sdir, "strength": slvl, "color": scolor,
            "bias": semi_bias,
            "low": round(semi_bias - 0.6, 1), "high": round(semi_bias + 0.6, 1),
            "text": f"{sdir} 경향 {slvl} (SOX {'+' if sox_pct_now>=0 else ''}{sox_pct_now}% · TSMC {'+' if tsmc_pct>=0 else ''}{tsmc_pct}%)",
        },
        "disclaimer": "야간 해외지표 기반 경향일 뿐, 갭은 자주 빗나가요. 진입은 시장 방향이 아니라 종목 자리(지지·수급)로 판단하세요.",
    }
    print(f"  📈 내일 경향: 코스피 {kdir}{klvl}({kospi_bias}%) · 반도체 {sdir}{slvl}({semi_bias}%)", file=sys.stderr)
    parts = []
    for k in ("sox", "nasdaq", "fx", "vix"):
        if k in out:
            parts.append(f"{out[k]['name']} {'+' if out[k]['pct']>=0 else ''}{out[k]['pct']}%")
    print(f"  🌐 시장 분위기: {label} (score {round(score,1)}) — {' / '.join(parts)}", file=sys.stderr)
    # ── 지수 연계 거래 변동성 경고 (선물옵션 만기·정기변경 달력 기반) ──
    _today = datetime.date.today()
    _y, _m, _d = _today.year, _today.month, _today.day
    _first = datetime.date(_y, _m, 1)
    _first_thu = 1 + (3 - _first.weekday()) % 7   # 첫 목요일
    _second_thu = _first_thu + 7                   # 둘째 목요일 (만기일)
    _is_quad = _m in (3, 6, 9, 12)                 # 분기월 = 동시만기
    _days_to_exp = _second_thu - _d
    expiry = {"warn": False, "level": "", "text": ""}
    if _days_to_exp == 0:
        if _is_quad:
            expiry = {"warn": True, "level": "high",
                      "text": "🔴 오늘 선물·옵션 동시만기일 — 프로그램 매물로 변동성 큼. 지수 연계 급등락 가능, 종목 자체 신호와 분리해서 보세요."}
        else:
            expiry = {"warn": True, "level": "mid",
                      "text": "🟡 오늘 옵션 만기일 — 장 마감 전후 프로그램 매매로 일시 변동성 가능."}
    elif 0 < _days_to_exp <= 2 and _is_quad:
        expiry = {"warn": True, "level": "mid",
                  "text": f"🟡 {_days_to_exp}일 후 선물·옵션 동시만기 — 만기 주간은 지수 변동성 확대 경향. 진입은 신중히."}
    if _m in (6, 12) and abs(_days_to_exp) <= 2:
        expiry["rebalance"] = "📊 코스피200 정기변경 시기 — 편입/편출 종목은 인덱스 수급 충격 가능."
    out["expiry"] = expiry
    return out


# ─── 적정주가 ──────────────────────────────────────────────────────────────
def fetch_naver_investor(code):
    """네이버 /trend API에서 외국인·기관·개인 수급 파싱
    - 당일 / 5일 / 20일 / 60일 누적 순매수 (단기~중장기 추세)
    - 외국인 연속 순매수/순매도 일수 + 5일 일별 흐름
    ※ 장중 실시간이 아닌 '전일 마감' 기준"""
    try:
        # 수급: 네이버 /api/trend (KRX 기준, 60일치). marketType=ALL이라 써도 이 엔드포인트는 KRX만 줌.
        # ※ 통합(KRX+NXT)은 front-api에 있으나 GitHub 서버 IP에서 차단돼 실효 없어 제거함.
        #   NXT는 한국 거래의 ~18%이고 수급 '방향'은 KRX만으로도 동일하게 읽힘.
        rows = []
        seen = set()
        try:
            d = http_json(
                f"https://m.stock.naver.com/api/stock/{code}/trend"
                f"?code={code}&marketType=ALL&pageSize=60",
                timeout=8
            )
            items = d if isinstance(d, list) else (d.get("result", []) if isinstance(d, dict) else [])
            for r in items:
                bd = r.get("bizdate", "")
                if bd and bd not in seen:
                    seen.add(bd)
                    rows.append(r)
        except Exception:
            pass
        if not rows:
            print(f"  ⚠ 수급 실패 ({code}): 데이터 없음", file=sys.stderr)
            return None
        print(f"  💰 수급 OK ({code}): {len(rows)}일치 · 최근 외국인 {rows[0].get('foreignerPureBuyQuant','?')}", file=sys.stderr)
        rows.sort(key=lambda r: r.get("bizdate", ""), reverse=True)

        def cum(field, n):
            return round(sum(to_n(r.get(field)) for r in rows[:n]))

        latest  = rows[0]
        foreign = round(to_n(latest.get("foreignerPureBuyQuant")))
        inst    = round(to_n(latest.get("organPureBuyQuant")))
        indiv   = round(to_n(latest.get("individualPureBuyQuant")))
        hold    = latest.get("foreignerHoldRatio", "")
        ndays   = len(rows)
        foreign5  = cum("foreignerPureBuyQuant", 5)
        foreign20 = cum("foreignerPureBuyQuant", 20) if ndays >= 15 else None
        foreign60 = cum("foreignerPureBuyQuant", 60) if ndays >= 45 else None
        inst5  = cum("organPureBuyQuant", 5)
        inst20 = cum("organPureBuyQuant", 20) if ndays >= 15 else None
        inst60 = cum("organPureBuyQuant", 60) if ndays >= 45 else None
        indiv5  = cum("individualPureBuyQuant", 5)
        indiv20 = cum("individualPureBuyQuant", 20) if ndays >= 15 else None
        indiv60 = cum("individualPureBuyQuant", 60) if ndays >= 45 else None
        # 5일 일별 외국인 순매수 (과거→최근, 미니 막대그래프용)
        n5 = min(5, len(rows))
        daily = [round(to_n(rows[i].get("foreignerPureBuyQuant"))) for i in range(n5)][::-1]
        daily_inst  = [round(to_n(rows[i].get("organPureBuyQuant"))) for i in range(n5)][::-1]
        daily_indiv = [round(to_n(rows[i].get("individualPureBuyQuant"))) for i in range(n5)][::-1]
        # 흐름 해석: 최근 절반 vs 이전 절반
        def calc_flow_label(d):
            if len(d) < 4: return ""
            half = len(d) // 2
            early, late = sum(d[:half]), sum(d[half:])
            if   late > 0 and early <= 0: return "최근 매수 전환"
            elif late < 0 and early >= 0: return "최근 매도 전환"
            elif late > 0 and early > 0:  return "꾸준히 매수"
            elif late < 0 and early < 0:  return "꾸준히 매도"
            elif late > early:            return "매수세 강화"
            elif late < early:            return "매수세 약화"
            return ""
        flow_label       = calc_flow_label(daily)
        flow_label_inst  = calc_flow_label(daily_inst)
        flow_label_indiv = calc_flow_label(daily_indiv)
        # 외국인 연속 순매수(+)/순매도(-) 일수
        streak = 0
        for r in rows:
            v = to_n(r.get("foreignerPureBuyQuant"))
            sign = 1 if v > 0 else -1 if v < 0 else 0
            if sign == 0:
                break
            if streak == 0 or (streak > 0) == (sign > 0):
                streak += sign
            else:
                break
        days = abs(streak)
        if foreign > 0:
            trend = f"외국인 {days}일 연속 순매수" if days >= 2 else "외국인 순매수"
        elif foreign < 0:
            trend = f"외국인 {days}일 연속 순매도" if days >= 2 else "외국인 순매도"
        else:
            trend = "중립"
        bd = latest.get("bizdate", "")
        datestr = f"{bd[4:6]}/{bd[6:8]}" if len(bd) == 8 else "전일"
        comment = (f"외국인 {'+' if foreign>=0 else ''}{foreign:,}주 · "
                   f"기관 {'+' if inst>=0 else ''}{inst:,}주 ({datestr} 기준)")
        f20s = f"{foreign20:+,}" if foreign20 is not None else "N/A"
        f60s = f"{foreign60:+,}" if foreign60 is not None else "N/A"
        print(f"  ✅ 네이버 수급 ({code}): {trend} | 5일 {foreign5:+,} / 20일 {f20s} / 60일 {f60s} ({ndays}일 확보)", file=sys.stderr)
        return {"foreign": foreign, "institution": inst, "individual": indiv,
                "foreign5": foreign5, "foreign20": foreign20, "foreign60": foreign60,
                "inst5": inst5, "inst20": inst20, "inst60": inst60,
                "indiv5": indiv5, "indiv20": indiv20, "indiv60": indiv60,
                "days": n5, "ndays": ndays,
                "daily": daily, "flowLabel": flow_label,
                "dailyInst": daily_inst, "flowLabelInst": flow_label_inst,
                "dailyIndiv": daily_indiv, "flowLabelIndiv": flow_label_indiv,
                "foreignTrend": trend, "comment": comment,
                "holdRatio": hold, "streak": streak, "date": datestr}
    except Exception as e:
        print(f"  네이버 수급 실패 ({code}): {e}", file=sys.stderr)
        return None


def fetch_naver_financial(code):
    """네이버 integration API에서 재무지표 + 컨센서스 목표주가 수집
    반환 dict: eps, bps, per, pbr, cns_per(추정PER), cns_eps(추정EPS),
               target_price(증권사 평균 목표주가), recomm_mean(투자의견 평균)"""
    def pnum(s):
        m = re.search(r'-?[\d,]+\.?\d*', str(s or ""))
        return float(m.group().replace(",", "")) if m else 0.0

    out = {"eps": None, "bps": None, "per": None, "pbr": None,
           "cns_per": None, "cns_eps": None, "target_price": None, "recomm_mean": None}
    try:
        d = http_json(f"https://m.stock.naver.com/api/stock/{code}/integration")
        for it in (d.get("totalInfos") or []):
            c = (it.get("code") or "").lower()
            v = pnum(it.get("value"))
            if   c == "per":    out["per"]     = round(v, 2) or None
            elif c == "pbr":    out["pbr"]     = round(v, 2) or None
            elif c == "eps":    out["eps"]     = round(v)    or None
            elif c == "bps":    out["bps"]     = round(v)    or None
            elif c == "cnsper": out["cns_per"] = round(v, 2) or None
            elif c == "cnseps": out["cns_eps"] = round(v)    or None
        ci = d.get("consensusInfo") or {}
        tp = pnum(ci.get("priceTargetMean"))
        if tp > 0: out["target_price"] = round(tp)
        rm = pnum(ci.get("recommMean"))
        if rm > 0: out["recomm_mean"] = round(rm, 2)
        if any(v for v in out.values()):
            print(f"  ✅ 네이버 ({code}): PER {out['per']} 추정PER {out['cns_per']} 목표가 {out['target_price']} EPS {out['eps']}", file=sys.stderr)
    except Exception as e:
        print(f"  네이버 integration 실패 ({code}): {e}", file=sys.stderr)

    return out


def fetch_fundamentals(code):
    """네이버 finance/annual에서 실적 추세 수집 → 거품/건전 판정.
    매출·영업이익 성장률(YoY), ROE, 부채비율, 추정이익 성장(컨센서스) 기반."""
    def pnum(s):
        if s in (None, "-", ""): return None
        m = re.search(r'-?[\d,]+\.?\d*', str(s))
        return float(m.group().replace(",", "")) if m else None

    out = {"revGrowth": None, "opGrowth": None, "roe": None, "debtRatio": None,
           "opMargin": None, "fwdEpsGrowth": None, "years": [], "grade": "정보없음",
           "gradeColor": "#8090b0", "comment": "실적 데이터 없음", "rows": {}}
    try:
        d = http_json(f"https://m.stock.naver.com/api/stock/{code}/finance/annual", timeout=8)
        fi = d.get("financeInfo") or {}
        cols = fi.get("trTitleList") or []
        keys = [c["key"] for c in cols]            # 예: ['202312','202412','202512','202612']
        isCns = {c["key"]: (c.get("isConsensus")=="Y") for c in cols}
        rows = {}
        for r in (fi.get("rowList") or []):
            t = r.get("title"); cc = r.get("columns") or {}
            rows[t] = {k: pnum((cc.get(k) or {}).get("value")) for k in keys}

        # 확정 실적 연도(컨센서스 제외)만으로 성장률 — 최근 2개 확정연도 비교
        actualKeys = [k for k in keys if not isCns.get(k)]
        def yoy(title):
            vals = [rows.get(title,{}).get(k) for k in actualKeys]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2 and vals[-2]:
                return round((vals[-1]-vals[-2])/abs(vals[-2])*100, 1)
            return None
        out["revGrowth"] = yoy("매출액")
        out["opGrowth"]  = yoy("영업이익")

        # 최신 확정연도 ROE·부채비율·영업이익률
        lastA = actualKeys[-1] if actualKeys else None
        if lastA:
            out["roe"]       = rows.get("ROE",{}).get(lastA)
            out["debtRatio"] = rows.get("부채비율",{}).get(lastA)
            out["opMargin"]  = rows.get("영업이익률",{}).get(lastA)

        # 추정이익 성장: 컨센서스연도 EPS vs 최신 확정 EPS
        cnsKeys = [k for k in keys if isCns.get(k)]
        if cnsKeys and lastA:
            epsNow = rows.get("EPS",{}).get(lastA)
            epsFwd = rows.get("EPS",{}).get(cnsKeys[0])
            if epsNow and epsFwd:
                out["fwdEpsGrowth"] = round((epsFwd-epsNow)/abs(epsNow)*100, 1)

        out["rows"] = {t: rows[t] for t in ("매출액","영업이익","ROE","부채비율") if t in rows}
        out["years"] = [c["title"] for c in cols]

        # ── 종합 판정 ──
        score = 0; reasons = []
        if out["fwdEpsGrowth"] is not None:
            if out["fwdEpsGrowth"] >= 20: score += 2; reasons.append(f"추정이익 +{out['fwdEpsGrowth']:.0f}% 성장")
            elif out["fwdEpsGrowth"] >= 0: score += 1
            else: score -= 2; reasons.append(f"추정이익 {out['fwdEpsGrowth']:.0f}% 역성장")
        if out["opGrowth"] is not None:
            if out["opGrowth"] >= 10: score += 1; reasons.append(f"영업이익 +{out['opGrowth']:.0f}%")
            elif out["opGrowth"] < 0: score -= 1; reasons.append(f"영업이익 {out['opGrowth']:.0f}%")
        if out["roe"] is not None:
            if out["roe"] >= 10: score += 1; reasons.append(f"ROE {out['roe']:.0f}%")
            elif out["roe"] < 5: score -= 1; reasons.append(f"ROE {out['roe']:.0f}% 낮음")
        if out["debtRatio"] is not None:
            if out["debtRatio"] <= 100: score += 1
            elif out["debtRatio"] >= 200: score -= 1; reasons.append(f"부채 {out['debtRatio']:.0f}%")

        if score >= 3:
            out["grade"]="✅ 실적 건전"; out["gradeColor"]="#00e5a0"
            out["comment"]="실적이 주가를 받쳐줘요. " + " · ".join(reasons[:3])
        elif score >= 0:
            out["grade"]="⚠️ 실적 보통"; out["gradeColor"]="#ffc940"
            out["comment"]="실적 받침이 평범해요. " + (" · ".join(reasons[:3]) or "성장 동력 약함")
        else:
            out["grade"]="🚫 거품 의심"; out["gradeColor"]="#ff4060"
            out["comment"]="실적이 주가를 못 따라가요. " + " · ".join(reasons[:3])

        print(f"  📊 실적 ({code}): {out['grade']} · 영익성장 {out['opGrowth']} · ROE {out['roe']} · 추정EPS성장 {out['fwdEpsGrowth']}", file=sys.stderr)
    except Exception as e:
        print(f"  실적 수집 실패 ({code}): {e}", file=sys.stderr)
    return out


def fetch_financial_data(yf_sym, code):
    """재무 dict 수집 — 네이버 1순위, Yahoo로 eps/bps 보강, fallback 최후
    반환: fetch_naver_financial과 같은 dict 구조"""
    fin = fetch_naver_financial(code)

    # eps/bps가 비면 Yahoo로 보강 (목표가·PER은 네이버 전용)
    if not (fin.get("eps") and fin.get("bps")):
        try:
            d = http_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}?interval=1d&range=1d")
            meta = d.get("chart", {}).get("result", [{}])[0].get("meta", {})
            eps_val = meta.get("epsTrailingTwelveMonths") or meta.get("eps")
            if eps_val and not fin.get("eps"):
                val = float(eps_val)
                fin["eps"] = round(val * 1350) if abs(val) < 1000 else round(val)
            bps_val = meta.get("bookValue")
            if bps_val and not fin.get("bps"):
                val = float(bps_val)
                fin["bps"] = round(val * 1350) if abs(val) < 1000 else round(val)
        except Exception as e:
            print(f"  Yahoo 재무 실패 ({yf_sym}): {e}", file=sys.stderr)

    # 최후: fallback EPS/BPS (2024 실적 기준) — 목표가/PER이 다 없을 때만 의미
    fallback = {
        "000660": {"eps": 27000,  "bps":  82000},
        "005930": {"eps":  4000,  "bps":  52000},
        "066570": {"eps":  7000,  "bps": 130000},
        "009150": {"eps": 10000,  "bps": 105000},
        "005380": {"eps": 36000,  "bps": 330000},
        "105560": {"eps": 15116,  "bps": 164669},
    }
    if code in fallback:
        if not fin.get("eps"): fin["eps"] = fallback[code]["eps"]
        if not fin.get("bps"): fin["bps"] = fallback[code]["bps"]

    return fin


def calc_fair_value(code, price, fin):
    """적정주가 = 증권사 컨센서스 목표주가 (메인).
    목표가가 없으면 현재PER→업종평균 역산, 그것도 없으면 PBR 역산으로 폴백.
    추정PER(cns_per)·현재PER은 보조 비교 지표로 함께 반환."""
    # 폴백용 업종 평균 멀티플
    sector_data = {
        "000660": {"per": 20, "pbr": 2.0, "name": "반도체"},
        "005930": {"per": 15, "pbr": 1.5, "name": "반도체"},
        "066570": {"per": 18, "pbr": 1.2, "name": "가전/전장"},
        "009150": {"per": 18, "pbr": 1.8, "name": "전자부품"},
        "005380": {"per": 10, "pbr": 0.8, "name": "자동차"},
        "105560": {"per": 9,  "pbr": 0.9, "name": "은행"},
        "017670": {"per": 12, "pbr": 1.0, "name": "통신"},
        "035420": {"per": 25, "pbr": 1.8, "name": "인터넷/플랫폼"},
    }
    sd = sector_data.get(code, {"per": 15, "pbr": 1.5, "name": "일반"})

    target  = fin.get("target_price") or 0
    cur_per = fin.get("per") or 0
    cur_pbr = fin.get("pbr") or 0
    cns_per = fin.get("cns_per") or 0
    eps     = fin.get("eps") or 0
    bps     = fin.get("bps") or 0

    results = {
        "sector": sd["name"],
        "sector_per": sd["per"],
        "current_per": cur_per,
        "cns_per": cns_per,
        "recomm": fin.get("recomm_mean") or 0,
        "target_price": round(target) if target else 0,
    }

    if target > 0:
        results["fair_value"] = round(target)
        results["basis"] = "목표주가"
    elif cur_per > 0:
        results["fair_value"] = round(price * sd["per"] / cur_per)
        results["basis"] = "PER"
    elif eps > 0:
        results["fair_value"] = round(eps * sd["per"])
        results["basis"] = "PER"
    elif cur_pbr > 0:
        results["fair_value"] = round(price * sd["pbr"] / cur_pbr)
        results["basis"] = "PBR"
    elif bps > 0:
        results["fair_value"] = round(bps * sd["pbr"])
        results["basis"] = "PBR"
    else:
        results["fair_value"] = 0
        results["basis"] = ""

    if results["fair_value"] > 0 and price > 0:
        gap = round((results["fair_value"] - price) / price * 100, 1)
        results["gap"] = gap
        if results["basis"] == "목표주가":
            results["gap_comment"] = (
                f"증권사 목표가 대비 {abs(gap)}% "
                f"{'상승여력' if gap > 5 else '하락위험' if gap < -5 else '근접'}"
            )
        else:
            results["gap_comment"] = (
                f"적정가 대비 {abs(gap)}% "
                f"{'저평가 — 매수 기회' if gap > 5 else '고평가 — 주의' if gap < -5 else '적정 수준'}"
            )
    else:
        results["gap"] = 0
        results["gap_comment"] = "데이터 부족"

    return results


# ─── DART ──────────────────────────────────────────────────────────────────
DART_CORP_CODE = {
    "000660": "00164779", "005930": "00126380", "066570": "00401731",
    "009150": "00164488", "005380": "00164742",
}

def fetch_dart(code, limit=5):
    dart_list = []
    if not DART_API_KEY:
        print("  DART API 키 없음", file=sys.stderr)
        return dart_list
    try:
        end   = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=90)).strftime("%Y%m%d")
        corp_code = DART_CORP_CODE.get(code, "")
        if corp_code:
            url = (f"https://opendart.fss.or.kr/api/list.json"
                   f"?crtfc_key={DART_API_KEY}&corp_code={corp_code}"
                   f"&bgn_de={start}&end_de={end}&page_count={limit}&sort=date&sort_mth=desc")
        else:
            url = (f"https://opendart.fss.or.kr/api/list.json"
                   f"?crtfc_key={DART_API_KEY}&stock_code={code}"
                   f"&bgn_de={start}&end_de={end}&page_count={limit}&sort=date&sort_mth=desc")
        d = http_json(url, timeout=10)
        if d.get("status") != "000":
            print(f"  DART 오류: {d.get('message','')}", file=sys.stderr)
            return dart_list
        important_kw = ["실적","분기","연간","배당","유상증자","무상증자","합병","분할","자사주","대규모","공개매수","주요사항"]
        for item in (d.get("list") or [])[:limit]:
            title = item.get("report_nm") or ""
            date  = item.get("rcept_dt") or ""
            rcept = item.get("rcept_no") or ""
            if title:
                dart_list.append({
                    "title": title[:50], "date": str(date)[:10],
                    "important": any(k in title for k in important_kw),
                    "corp": item.get("corp_name") or "",
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}" if rcept else "",
                })
        print(f"  ✅ DART {code}: {len(dart_list)}건", file=sys.stderr)
    except Exception as e:
        print(f"  DART 실패 ({code}): {e}", file=sys.stderr)
    return dart_list


# ─── Yahoo Finance OHLCV ───────────────────────────────────────────────────
def fetch_yahoo_ohlcv(yf_sym, interval="1d", range_="60d"):
    for base in ["query1", "query2"]:
        try:
            url = (f"https://{base}.finance.yahoo.com/v8/finance/chart/{yf_sym}"
                   f"?interval={interval}&range={range_}&includePrePost=false")
            d = http_json(url)
            result = d.get("chart", {}).get("result", [None])[0]
            if not result: continue
            meta = result.get("meta", {})
            ts = result.get("timestamp", []) or []
            q = result.get("indicators", {}).get("quote", [{}])[0]
            candles = []
            for i, t in enumerate(ts):
                close = (q.get("close") or [])[i] if i < len(q.get("close") or []) else None
                if close is None: continue
                candles.append({
                    "open":   (q.get("open")   or [0]*len(ts))[i] or 0,
                    "high":   (q.get("high")   or [0]*len(ts))[i] or 0,
                    "low":    (q.get("low")    or [0]*len(ts))[i] or 0,
                    "close":  close,
                    "volume": (q.get("volume") or [0]*len(ts))[i] or 0,
                })
            if len(candles) >= 5:
                return meta, candles
        except Exception as e:
            print(f"  Yahoo 실패 ({yf_sym}/{interval}): {e}", file=sys.stderr)
    return {}, []


# ─── 기술 지표 ──────────────────────────────────────────────────────────────
def ema(arr, p):
    if len(arr) < p: return [None] * len(arr)
    k = 2 / (p + 1); e = sum(arr[:p]) / p
    res = [None] * (p - 1) + [e]
    for i in range(p, len(arr)):
        e = arr[i] * k + e * (1 - k); res.append(e)
    return res

def calc_rsi(closes, p=14):
    if len(closes) < p + 1: return None
    ag = al = 0
    for i in range(1, p + 1):
        d = closes[i] - closes[i - 1]
        if d > 0: ag += d
        else: al -= d
    ag /= p; al /= p
    for i in range(p + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (p - 1) + max(d, 0)) / p
        al = (al * (p - 1) + max(-d, 0)) / p
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 1)

def calc_macd(closes):
    if len(closes) < 35: return None, None, None
    e12, e26 = ema(closes, 12), ema(closes, 26)
    ml = [a - b for a, b in zip(e12, e26) if a is not None and b is not None]
    if len(ml) < 9: return None, None, None
    sig = ema(ml, 9)
    base = closes[-1] if closes[-1] != 0 else 1
    m = round(ml[-1] / base * 100, 3); s = round(sig[-1] / base * 100, 3)
    return m, s, round(m - s, 3)

def calc_stoch(highs, lows, closes, p=14):
    if len(closes) < p: return None
    hh, ll = max(highs[-p:]), min(lows[-p:])
    return 50.0 if hh == ll else round((closes[-1] - ll) / (hh - ll) * 100, 1)

def calc_stoch_rsi(closes, rsi_period=14, stoch_period=14, k=3, d=3):
    if len(closes) < 35: return None
    rsi_series = []
    for i in range(rsi_period, len(closes)):
        r = calc_rsi(closes[:i + 1], rsi_period)
        if r is not None: rsi_series.append(r)
    if len(rsi_series) < stoch_period + d: return None
    raw_k = []
    for i in range(stoch_period - 1, len(rsi_series)):
        window = rsi_series[i - stoch_period + 1: i + 1]
        hh, ll = max(window), min(window)
        kv = 50.0 if hh == ll else (rsi_series[i] - ll) / (hh - ll) * 100
        raw_k.append(kv)
    if len(raw_k) < d * 2: return None
    k_smoothed = []
    for i in range(d - 1, len(raw_k)):
        k_smoothed.append(sum(raw_k[i - d + 1: i + 1]) / d)
    if len(k_smoothed) < d: return None
    k_val = k_smoothed[-1]; d_val = sum(k_smoothed[-d:]) / d
    if k_val < 20 and d_val < 20: signal, comment = "매수", f"과매도 구간({k_val:.0f}) — 반등 임박"
    elif k_val > 80 and d_val > 80: signal, comment = "매도", f"과매수 구간({k_val:.0f}) — 조정 주의"
    elif k_val > d_val and k_val < 30: signal, comment = "매수", "저점 골든크로스"
    elif k_val < d_val and k_val > 70: signal, comment = "매도", "고점 데드크로스"
    else: signal, comment = "중립", f"K {k_val:.0f} / D {d_val:.0f}"
    return {"k": round(k_val, 1), "d": round(d_val, 1), "signal": signal, "comment": comment}

def detect_divergence(closes, highs, lows, lookback=20):
    if len(closes) < lookback + 14: return None
    recent_low_idx  = lows[-lookback:].index(min(lows[-lookback:])) + (len(lows) - lookback)
    recent_high_idx = highs[-lookback:].index(max(highs[-lookback:])) + (len(highs) - lookback)
    if recent_low_idx < 14 or recent_high_idx < 14: return None
    prev_window_lows  = lows[max(0, recent_low_idx - lookback):recent_low_idx]
    prev_window_highs = highs[max(0, recent_high_idx - lookback):recent_high_idx]
    if not prev_window_lows or not prev_window_highs: return None
    prev_low_idx  = prev_window_lows.index(min(prev_window_lows)) + max(0, recent_low_idx - lookback)
    prev_high_idx = prev_window_highs.index(max(prev_window_highs)) + max(0, recent_high_idx - lookback)
    def rsi_at(idx):
        if idx < 14: return None
        return calc_rsi(closes[:idx + 1], 14)
    rsi_recent_low  = rsi_at(recent_low_idx);  rsi_prev_low  = rsi_at(prev_low_idx)
    rsi_recent_high = rsi_at(recent_high_idx); rsi_prev_high = rsi_at(prev_high_idx)
    bullish = bearish = False; comment = ""
    if (rsi_recent_low is not None and rsi_prev_low is not None
            and lows[recent_low_idx] < lows[prev_low_idx]
            and rsi_recent_low > rsi_prev_low and rsi_recent_low < 40):
        bullish = True
        comment = f"강세 다이버전스 — 주가 ↓ / RSI ↑ ({rsi_prev_low:.0f}→{rsi_recent_low:.0f}). 바닥권 반등 신호"
    if (rsi_recent_high is not None and rsi_prev_high is not None
            and highs[recent_high_idx] > highs[prev_high_idx]
            and rsi_recent_high < rsi_prev_high and rsi_recent_high > 60):
        bearish = True
        if comment: comment += " | "
        comment += f"약세 다이버전스 — 주가 ↑ / RSI ↓ ({rsi_prev_high:.0f}→{rsi_recent_high:.0f})"
    if not bullish and not bearish: return None
    return {"bullish": bullish, "bearish": bearish,
            "signal": "매수" if bullish and not bearish else "매도" if bearish and not bullish else "혼조",
            "comment": comment}

def calc_ichimoku(highs, lows, closes):
    if len(closes) < 52: return None
    def mid(h, l): return (max(h) + min(l)) / 2
    tenkan  = mid(highs[-9:],  lows[-9:])
    kijun   = mid(highs[-26:], lows[-26:])
    senkou_a = (tenkan + kijun) / 2
    senkou_b = mid(highs[-52:], lows[-52:])
    price = closes[-1]
    cloud_top = max(senkou_a, senkou_b); cloud_bot = min(senkou_a, senkou_b)
    if price > cloud_top and tenkan > kijun:
        signal, comment = "매수", "구름대 위 + 전환선>기준선 — 강한 상승 추세"
    elif price > cloud_top:
        signal, comment = "매수", "구름대 위 — 상승 우위"
    elif price < cloud_bot and tenkan < kijun:
        signal, comment = "매도", "구름대 아래 + 전환선<기준선 — 강한 하락 추세"
    elif price < cloud_bot:
        signal, comment = "매도", "구름대 아래 — 하락 우위"
    else:
        signal, comment = "중립", "구름대 내부 — 방향성 미정"
    return {"tenkan": round(tenkan), "kijun": round(kijun),
            "senkouA": round(senkou_a), "senkouB": round(senkou_b),
            "cloudTop": round(cloud_top), "cloudBot": round(cloud_bot),
            "signal": signal, "comment": comment}

def calc_cci(highs, lows, closes, p=20):
    if len(closes) < p: return None
    typical = [(h + l + c) / 3 for h, l, c in zip(highs[-p:], lows[-p:], closes[-p:])]
    sma_tp = sum(typical) / p
    mad = sum(abs(t - sma_tp) for t in typical) / p
    if mad == 0: return {"value": 0, "signal": "중립", "comment": "변동성 없음"}
    cci = (typical[-1] - sma_tp) / (0.015 * mad)
    if cci > 200:   signal, comment = "매도", f"극단 과매수({cci:.0f})"
    elif cci > 100: signal, comment = "매도", f"과매수({cci:.0f})"
    elif cci < -200: signal, comment = "매수", f"극단 과매도({cci:.0f})"
    elif cci < -100: signal, comment = "매수", f"과매도({cci:.0f})"
    else: signal, comment = "중립", f"중립({cci:.0f})"
    return {"value": round(cci, 1), "signal": signal, "comment": comment}

def calc_psar(highs, lows, closes, af_start=0.02, af_step=0.02, af_max=0.20):
    if len(closes) < 10: return None
    trend_up = closes[1] > closes[0]
    psar = lows[0] if trend_up else highs[0]
    ep = highs[1] if trend_up else lows[1]; af = af_start
    for i in range(2, len(closes)):
        prev_psar = psar
        psar = prev_psar + af * (ep - prev_psar)
        if trend_up:
            psar = min(psar, lows[i-1], lows[i-2] if i >= 2 else lows[i-1])
            if lows[i] < psar:
                trend_up = False; psar = ep; ep = lows[i]; af = af_start
            else:
                if highs[i] > ep: ep = highs[i]; af = min(af + af_step, af_max)
        else:
            psar = max(psar, highs[i-1], highs[i-2] if i >= 2 else highs[i-1])
            if highs[i] > psar:
                trend_up = True; psar = ep; ep = highs[i]; af = af_start
            else:
                if lows[i] < ep: ep = lows[i]; af = min(af + af_step, af_max)
    gap_pct = abs(closes[-1] - psar) / closes[-1] * 100
    recent_flip = gap_pct < 1.5
    trend = "상승" if trend_up else "하락"
    if trend_up:
        signal = "매수"; comment = f"상승 추세 유지 (손절선 ₩{round(psar):,})"
        if recent_flip: comment += " — 전환 주의"
    else:
        signal = "매도"; comment = f"하락 추세 (저항선 ₩{round(psar):,})"
        if recent_flip: comment += " — 상승 전환 가능성"
    return {"psar": round(psar), "trend": trend, "signal": signal,
            "comment": comment, "nearFlip": recent_flip}

def calc_value_surge(closes, volumes, p=20):
    if len(closes) < p + 1: return None
    values = [c * v for c, v in zip(closes, volumes)]
    recent = values[-1]; avg = sum(values[-p-1:-1]) / p
    if avg == 0: return {"ratio": 1.0, "surge": False, "comment": "데이터 부족"}
    ratio = recent / avg
    if ratio > 3.0:   comment, surge = f"거래대금 급증 ({ratio:.1f}배) — 강한 세력 진입", True
    elif ratio > 2.0: comment, surge = f"거래대금 증가 ({ratio:.1f}배)", True
    elif ratio > 1.5: comment, surge = f"거래대금 소폭 증가 ({ratio:.1f}배)", False
    elif ratio < 0.5: comment, surge = f"거래대금 감소 ({ratio:.1f}배)", False
    else: comment, surge = f"거래대금 평이 ({ratio:.1f}배)", False
    return {"ratio": round(ratio, 2), "surge": surge, "comment": comment}

def calc_sma(arr, p):
    if len(arr) < p: return None
    return round(sum(arr[-p:]) / p)

def calc_williams(highs, lows, closes, p=14):
    if len(closes) < p: return None
    hh, ll = max(highs[-p:]), min(lows[-p:])
    return -50.0 if hh == ll else round((hh - closes[-1]) / (hh - ll) * -100, 1)

def calc_adx(highs, lows, closes, p=14):
    if len(closes) < p + 1: return None
    tr, pdm, ndm = [], [], []
    for i in range(1, len(closes)):
        h, l, ph, pl, pc = highs[i], lows[i], highs[i-1], lows[i-1], closes[i-1]
        tr.append(max(h-l, abs(h-pc), abs(l-pc)))
        pdm.append(max(h-ph, 0) if max(h-ph, 0) > (pl-l) else 0)
        ndm.append(max(pl-l, 0) if max(pl-l, 0) > (h-ph) else 0)
    atr = sum(tr[-p:]) / p
    if atr == 0: return None
    pdi = sum(pdm[-p:]) / p / atr * 100; ndi = sum(ndm[-p:]) / p / atr * 100
    dx = 0 if (pdi + ndi) == 0 else abs(pdi - ndi) / (pdi + ndi) * 100
    return {"adx": round(dx, 1), "pdi": round(pdi, 1), "ndi": round(ndi, 1),
            "strength": "강한 추세" if dx > 25 else "약한 추세" if dx > 20 else "추세 없음(횡보)"}

def calc_mfi(highs, lows, closes, volumes, p=14):
    if len(closes) < p + 1: return None
    pmf = nmf = 0
    for i in range(len(closes) - p, len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        ptp = (highs[i-1] + lows[i-1] + closes[i-1]) / 3
        mf = tp * volumes[i]
        if tp > ptp: pmf += mf
        else: nmf += mf
    return 100.0 if nmf == 0 else round(100 - 100 / (1 + pmf / nmf), 1)

def calc_vwap(highs, lows, closes, volumes, p=5):
    if len(closes) < p: return None
    tv = sv = 0
    for i in range(-p, 0):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        tv += tp * volumes[i]; sv += volumes[i]
    return None if sv == 0 else round(tv / sv)

def calc_boll(closes, p=20):
    if len(closes) < p: return None
    sl = closes[-p:]; m = sum(sl) / p
    std = (sum((v - m) ** 2 for v in sl) / p) ** 0.5
    return {"upper": m + 2 * std, "mid": m, "lower": m - 2 * std}

def calc_atr(highs, lows, closes, p=14):
    """평균 실질 변동폭 — 종목별 변동성. 손절폭 산정에 사용."""
    if len(closes) < p + 1: return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        trs.append(tr)
    atr = sum(trs[-p:]) / p
    pct = round(atr / closes[-1] * 100, 2) if closes[-1] else 0
    return {"atr": round(atr), "pct": pct}

def calc_pivot(highs, lows, closes):
    if len(closes) < 2: return None
    h, l, c = highs[-2], lows[-2], closes[-2]; p = (h + l + c) / 3
    return {"p": round(p), "r1": round(2*p-l), "r2": round(p+(h-l)),
            "s1": round(2*p-h), "s2": round(p-(h-l))}

def calc_obv(closes, volumes):
    if len(closes) < 2: return {"obv": 0, "slope": 0, "trend": "횡보"}
    obv = 0; arr = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]: obv += volumes[i]
        elif closes[i] < closes[i-1]: obv -= volumes[i]
        arr.append(obv)
    recent = arr[-5:]; base = recent[0] if recent[0] != 0 else 1
    slope = (recent[-1] - recent[0]) / abs(base) * 100
    return {"obv": arr[-1], "slope": round(slope, 1),
            "trend": "상승" if slope > 1 else "하락" if slope < -1 else "횡보"}

def detect_patterns(opens, highs, lows, closes):
    pat = []
    if len(closes) < 3: return pat
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    po, pc = opens[-2], closes[-2]
    body, rng = abs(c - o), h - l
    if rng <= 0: return pat
    if body < rng * 0.1: pat.append({"name": "도지", "type": "반전경고", "color": "#ffd166"})
    if c > o and (o-l) > body*2 and (h-c) < body*0.5: pat.append({"name": "망치형", "type": "상승반전", "color": "#00e676"})
    if c > o and (h-c) > body*2 and (o-l) < body*0.5: pat.append({"name": "역망치", "type": "상승반전", "color": "#00e676"})
    if c < o and (h-o) > body*2 and (c-l) < body*0.5: pat.append({"name": "유성형", "type": "하락반전", "color": "#ff4d6d"})
    if pc > po and c < o and o < pc and c > po: pat.append({"name": "강세장악", "type": "상승반전", "color": "#00e676"})
    if po > pc and o < c and c < po and o > pc: pat.append({"name": "약세장악", "type": "하락반전", "color": "#ff4d6d"})
    if c > o and body > rng * 0.7: pat.append({"name": "장대양봉", "type": "강한상승", "color": "#00e676"})
    if o > c and body > rng * 0.7: pat.append({"name": "장대음봉", "type": "강한하락", "color": "#ff4d6d"})
    return pat[:3]

def fear_greed(rsi, stoch, obv_slope, adx_val, vol_ratio):
    score = 50
    if rsi is not None: score += (rsi - 50) * 0.5
    if stoch is not None: score += (stoch - 50) * 0.15
    if obv_slope is not None: score += max(min(obv_slope, 10), -10) * 0.3
    if adx_val is not None: score += 3 if adx_val > 30 else (-3 if adx_val < 15 else 0)
    if vol_ratio is not None: score += 4 if vol_ratio > 1.5 else (-4 if vol_ratio < 0.6 else 0)
    score = min(max(round(score), 0), 100)
    label = ("극단적 탐욕" if score >= 75 else "탐욕" if score >= 60
             else "중립" if score >= 40 else "공포" if score >= 25 else "극단적 공포")
    color = ("#ff4d6d" if score >= 75 else "#ffd166" if score >= 60
             else "#9ab" if score >= 40 else "#ffd166" if score >= 25 else "#00e676")
    return {"score": score, "label": label, "color": color}

def calc_weekly_signal(weekly_candles):
    if len(weekly_candles) < 10:
        return {"opinion": "중립", "rsi": None, "macd": None, "comment": "데이터 부족"}
    closes = [c["close"] for c in weekly_candles]
    rsi = calc_rsi(closes); macd, sig, hist = calc_macd(closes)
    opinion = "중립"
    if rsi and macd and sig:
        if rsi < 45 and macd > sig: opinion = "매수"
        elif rsi > 60 and macd < sig: opinion = "매도"
    comment = f"주봉 RSI {rsi or '-'} · MACD {'골든크로스' if macd and sig and macd > sig else '데드크로스' if macd and sig else '-'}"
    return {"opinion": opinion, "rsi": rsi, "macd": macd, "macdSignal": sig, "comment": comment}

def calc_relative_strength(stock_pct, kospi_pct):
    if kospi_pct == 0: return {"rs": 0, "comment": "KOSPI 데이터 없음", "strong": False, "indexLinked": False, "indexNote": ""}
    rs = round(stock_pct - kospi_pct, 2); strong = rs > 0
    comment = (f"KOSPI 대비 +{rs}%p 강세" if rs > 1 else
               f"KOSPI 대비 {rs}%p 약세" if rs < -1 else "KOSPI 대비 중립")
    # 지수 동조도: 종목이 코스피와 거의 같이 움직이면 지수 연계 거래에 휘둘리는 중
    # (자기 재료 없이 지수 따라감 = 프로그램/ETF 영향 추정)
    index_linked = False
    index_note = ""
    if abs(kospi_pct) >= 0.5:  # 지수가 의미있게 움직인 날만 판단
        if abs(rs) <= 0.3:     # 종목이 지수와 거의 동일하게 움직임
            index_linked = True
            direction = "하락" if kospi_pct < 0 else "상승"
            index_note = (f"📐 지수 동조 — 코스피와 거의 같이 {direction} 중. "
                          f"종목 자체 재료보다 지수 연계 거래(ETF·선물·프로그램)에 휘둘리는 흐름일 수 있어요. "
                          f"지수가 진정되면 종목도 제자리 찾을 가능성.")
    return {"rs": rs, "comment": comment, "strong": strong,
            "indexLinked": index_linked, "indexNote": index_note}

def check_52w_breakout(price, high52w, low52w):
    if high52w == 0: return {"nearHigh": False, "nearLow": False, "position": 50, "comment": ""}
    # 52주 저점이 0이거나 비정상이면 고점 기준으로만 판단 (position 왜곡 방지)
    if not low52w or low52w <= 0 or high52w <= low52w:
        pos = min(round(price / high52w * 100), 100)
        near_high = pos >= 90
        comment = (f"52주 신고가 근접 ({pos}%)" if near_high else f"52주 위치 ({pos}%, 저점 미확보)")
        return {"nearHigh": near_high, "nearLow": False, "position": pos, "comment": comment}
    pos = round((price - low52w) / (high52w - low52w) * 100) if high52w != low52w else 50
    near_high = pos >= 90; near_low = pos <= 10
    comment = (f"52주 신고가 근접 ({pos}%)" if near_high else
               f"52주 신저가 근접 ({pos}%)" if near_low else f"52주 중간 위치 ({pos}%)")
    return {"nearHigh": near_high, "nearLow": near_low, "position": pos, "comment": comment}

def check_volume_surge(volumes):
    if len(volumes) < 10: return {"surge": False, "ratio": 1.0, "comment": "데이터 부족"}
    avg = sum(volumes[-20:]) / min(20, len(volumes))
    latest = volumes[-1]; ratio = round(latest / avg, 2) if avg > 0 else 1.0
    surge = ratio >= 2.0
    comment = (f"거래량 급증 ({ratio}x)" if ratio >= 2.0 else
               f"거래량 증가 ({ratio}x)" if ratio >= 1.3 else f"거래량 평이 ({ratio}x)")
    return {"surge": surge, "ratio": ratio, "comment": comment}

def contra_signal(rsi, macd, macd_sig, obv, patterns, fg_score, investor):
    signals, strength = [], 0
    if rsi and rsi > 65 and obv and obv["slope"] < -1:
        signals.append("🔴 RSI 고점 + OBV 하락 → 가격 하락 선행 신호"); strength -= 2
    if rsi and rsi < 35 and obv and obv["slope"] > 1:
        signals.append("🟢 RSI 저점 + OBV 상승 → 세력 매집 가능성"); strength += 2
    if fg_score < 25: signals.append("🟢 극단적 공포 → 역발상 매수 기회"); strength += 2
    if fg_score > 75: signals.append("🔴 극단적 탐욕 → 역발상 매도 기회"); strength -= 2
    if macd and macd_sig:
        if macd > macd_sig and rsi and rsi > 70:
            signals.append("⚠️ MACD 상승 + RSI 과매수 → 신호 과잉 주의")
        if macd < macd_sig and rsi and rsi < 30:
            signals.append("⚠️ MACD 하락 + RSI 과매도 → 신호 과잉 주의")
    if investor:
        f, inst = investor.get("foreign", 0), investor.get("institution", 0)
        if f < 0 and inst < 0 and rsi and rsi < 35:
            signals.append("🟢 외국인·기관 매도 + RSI 과매도 → 패닉셀 역매수"); strength += 1
        if f > 0 and inst > 0 and rsi and rsi > 70:
            signals.append("⚠️ 외국인·기관 매수 + RSI 과매수 → 고점 매수 주의"); strength -= 1
    bull = len([p for p in patterns if "상승" in p["type"]])
    bear = len([p for p in patterns if "하락" in p["type"]])
    if bull >= 2: signals.append("⚠️ 상승 패턴 다수 → 차익실현 주의"); strength -= 1
    if bear >= 2: signals.append("⚠️ 하락 패턴 다수 → 역매수 기회 탐색"); strength += 1
    action = ("역발상 매수 기회" if strength >= 2 else
              "역발상 매도 기회" if strength <= -2 else "현 신호 유효")
    return {"signals": signals[:4], "action": action, "strength": strength}

def master_signal(rsi, macd, macd_sig, stoch, wr, mfi, adx, obv,
                  closes, price, h52, l52, vwap, weekly_opinion,
                  investor, short_ratio, news_list,
                  stoch_rsi=None, divergence=None,
                  ichimoku=None, cci=None, psar=None, value_surge=None,
                  boll_data=None, weekly_rsi=None, patterns=None, fx_price=0):
    score = 0
    # RSI
    if rsi: score += 2 if rsi < 30 else 1 if rsi < 45 else -2 if rsi > 70 else -1 if rsi > 60 else 0
    # MACD
    if macd and macd_sig: score += 2 if macd > macd_sig else -2
    # Williams %R: -70 이하로 문턱 낮춤 (기존 -80)
    if wr: score += 1 if wr < -70 else -1 if wr > -20 else 0
    # 스토캐스틱
    if stoch: score += 1 if stoch < 20 else -1 if stoch > 80 else 0
    # MFI: 범위 확장 (기존 극단만 → 40/60으로 확장)
    if mfi: score += 2 if mfi < 20 else 1 if mfi < 40 else -2 if mfi > 80 else -1 if mfi > 60 else 0
    # OBV: +1 → +2로 강화 (수급 대체)
    if obv: score += 2 if obv["slope"] > 2 else -2 if obv["slope"] < -2 else 0
    # ADX: 횡보 구간 반등 가능성 반영
    if adx:
        if adx["adx"] < 15: score += 1    # 추세 없음 → 반등 여지
        elif adx["adx"] > 30 and adx["pdi"] < adx["ndi"]: score -= 1  # 강한 하락 추세
    # 스토캐스틱 RSI: 과매도 구간 차등 (기존 일괄 +2 → K<10이면 +3)
    if stoch_rsi:
        if stoch_rsi["signal"] == "매수":
            score += 3 if stoch_rsi.get("k", 50) < 10 else 2
        elif stoch_rsi["signal"] == "매도":
            score -= 2
    # 다이버전스: +3 → +4로 강화
    if divergence:
        if divergence.get("bullish"): score += 4
        if divergence.get("bearish"): score -= 4
    # 일목
    if ichimoku:
        if ichimoku["signal"] == "매수": score += 2 if "강한" in ichimoku["comment"] else 1
        elif ichimoku["signal"] == "매도": score -= 2 if "강한" in ichimoku["comment"] else 1
    # CCI
    if cci:
        if cci["signal"] == "매수": score += 2 if "극단" in cci["comment"] else 1
        elif cci["signal"] == "매도": score -= 2 if "극단" in cci["comment"] else 1
    # PSAR
    if psar:
        if psar["signal"] == "매수": score += 1
        elif psar["signal"] == "매도": score -= 1
    # 거래대금 급등
    if value_surge and value_surge["surge"]:
        score += 1 if rsi and rsi > 50 else -1 if rsi and rsi < 50 else 0
    # 52주 위치: +1 → +2로 강화, 볼린저 밴드 위치 추가
    if h52 > 0 and l52 > 0:
        pos = (price - l52) / (h52 - l52) * 100
        score += 2 if pos < 20 else 1 if pos < 35 else -2 if pos > 90 else -1 if pos > 85 else 0
    # 볼린저 밴드 위치 (boll_pos가 있으면)
    if boll_data and boll_data.get("position") is not None:
        bp = boll_data["position"]
        score += 2 if bp < 15 else 1 if bp < 25 else -2 if bp > 85 else -1 if bp > 75 else 0
    # VWAP
    if vwap: score += 1 if price > vwap else -1
    # 이격도: SMA20 대비 -8% 이상 이격이면 평균회귀 가능성
    s5, s20 = calc_sma(closes, 5), calc_sma(closes, 20)
    if s5 and s20:
        score += 1 if s5 > s20 else -1
    if s20 and price > 0:
        gap = (price - s20) / s20 * 100
        score += 2 if gap < -8 else 1 if gap < -4 else -1 if gap > 8 else 0
    # 주봉
    if weekly_opinion == "매수": score += 2
    elif weekly_opinion == "매도": score -= 2
    # 주봉 RSI 과매도 추가 (+1)
    if weekly_rsi and weekly_rsi < 40: score += 1
    elif weekly_rsi and weekly_rsi > 70: score -= 1
    # 공매도
    if short_ratio > 0:
        if short_ratio > 5: score -= 1
        elif short_ratio < 1: score += 1
    # 뉴스
    if news_list:
        pos_count = len([n for n in news_list if n["sentiment"] == "긍정"])
        neg_count = len([n for n in news_list if n["sentiment"] == "부정"])
        if pos_count > neg_count: score += 1
        elif neg_count > pos_count: score -= 1
    # 캔들 패턴: +1 → +2로 강화
    if patterns:
        pos_pat = [p for p in patterns if p.get("type") in ["상승반전","강세"]]
        neg_pat = [p for p in patterns if p.get("type") in ["하락반전","약세"]]
        if pos_pat: score += 2
        if neg_pat: score -= 2
    # 수급은 화면에서 직접 확인 후 판단 (점수에서 제외)
    # ── 레짐 구분 + 외국인 수급 강화 (학술 근거: 추세장에선 과매수=매도가 해롭다) ──
    # 과매수 페널티를 재계산해서, 추세장(ADX>20 & 일목매수)이면 절반을 되돌려줌
    ob_penalty = 0
    if rsi:
        if rsi > 70: ob_penalty -= 2
        elif rsi > 60: ob_penalty -= 1
    if h52 > 0 and l52 > 0:
        _pos = (price - l52) / (h52 - l52) * 100
        if _pos > 90: ob_penalty -= 2
        elif _pos > 85: ob_penalty -= 1
    if boll_data and boll_data.get("position") is not None:
        _bp = boll_data["position"]
        if _bp > 85: ob_penalty -= 2
        elif _bp > 75: ob_penalty -= 1
    if stoch and stoch > 80: ob_penalty -= 1
    if 's20' in dir() and s20 and price > 0:
        pass
    _adx_val = adx.get("adx") if adx else None
    _ichi_buy = ichimoku and ichimoku.get("signal") == "매수"
    _uptrend = _adx_val is not None and _adx_val > 20 and _ichi_buy
    if _uptrend and ob_penalty < 0:
        # 추세장: 과매수 페널티 절반 되돌림 (예: -8 → +4 보정)
        relief = ob_penalty - round(ob_penalty / 2)  # 음수 페널티의 절반만큼 +
        score -= relief  # relief가 음수이므로 score 상승
    # 외국인 수급 강화 (한국시장: 외국인=숙련 차익거래자, 모멘텀 주도)
    if investor:
        _f5 = investor.get("foreign5", 0) or 0
        _streak = investor.get("streak", 0) or 0
        if _f5 > 0 or _streak >= 2:
            score += 1   # 외국인 순매수 흐름 → 매수 가산
        elif _f5 < -1000000:
            score -= 1   # 외국인 대량 순매도 → 매도 경계 유지
    # ── 환율 위기 + 외국인 보유율 차별 경계 ──
    # 환율이 위험 수준이면, 외국인 보유율 높은 종목일수록 자금이탈에 취약
    # (모든 종목 일괄 차감이 아니라 외국인 비중으로 차별화 → 종목 우열 유지)
    if fx_price and fx_price >= 1550 and investor:
        try:
            _hold = float(str(investor.get("holdRatio", "0")).replace("%", ""))
        except (ValueError, TypeError):
            _hold = 0
        if _hold >= 50:   score -= 2   # 외국인 절반 이상 보유 → 환율위기에 크게 취약
        elif _hold >= 30: score -= 1   # 외국인 상당 보유 → 일부 취약
        # 30% 미만은 환율 영향 적어 차감 없음 (내수·개인 비중 높은 종목)
    score = round(score)
    # ──────────────────────────────────────────────────
    opinion = "매수" if score >= 6 else "매도" if score <= -5 else "중립"
    # 중립의 결: 점수가 어느 쪽으로 기울었는지 (매수문턱 6 / 매도문턱 -5)
    nuance = ""
    if opinion == "중립":
        if   score >= 4:  nuance = "매수 우위 (문턱 근접)"
        elif score >= 1:  nuance = "약한 매수 우위"
        elif score == 0:  nuance = "완전 중립 (관망)"
        elif score >= -2: nuance = "약한 매도 우위"
        else:             nuance = "매도 우위 (문턱 근접)"
    return opinion, score, nuance

def assess_cautious_entry(opinion, score, ichimoku, stoch_rsi, divergence,
                          psar, investor, cci, price, pivot):
    result = {"entry": False, "signals": [], "reason": "", "stopLoss": 0}
    if opinion != "중립" or score < 2 or score > 5: return result
    matched = []
    if ichimoku and ichimoku.get("signal") == "매수":
        matched.append("일목균형표 " + ("강세" if "강한" in ichimoku.get("comment","") else "매수"))
    if stoch_rsi and stoch_rsi.get("signal") == "매수":
        matched.append("스토캐스틱 RSI 매수")
    if divergence and divergence.get("bullish"):
        matched.append("강세 다이버전스")
    if psar and psar.get("signal") == "매수":
        matched.append("파라볼릭 SAR 상승")
    if cci and cci.get("signal") == "매수":
        matched.append("CCI " + ("극단 과매도" if "극단" in cci.get("comment","") else "과매도"))
    bearish_count = sum([
        1 if ichimoku and ichimoku.get("signal") == "매도" else 0,
        1 if stoch_rsi and stoch_rsi.get("signal") == "매도" else 0,
        1 if divergence and divergence.get("bearish") else 0,
        1 if psar and psar.get("signal") == "매도" else 0,
    ])
    if len(matched) >= 3 and bearish_count < 2:
        result["entry"] = True; result["signals"] = matched
        if psar and psar.get("psar"):
            result["stopLoss"] = psar["psar"]
        elif pivot and pivot.get("s1"):
            result["stopLoss"] = pivot["s1"]
        else:
            result["stopLoss"] = round(price * 0.95)
        result["reason"] = f"중립이지만 매수 신호 {len(matched)}개 확인 — 소량 진입 검토 가능"
    return result

def assess_overheat_warning(rsi, stoch_rsi, price, high52w, fair_value, fg_score):
    reasons = []
    if rsi and rsi >= 70:
        k = stoch_rsi.get("k") if stoch_rsi else None
        if k is not None and k >= 80: reasons.append(f"다중 과매수 (RSI {rsi:.0f} · 스토캐스틱 {k:.0f})")
        elif rsi >= 75: reasons.append(f"RSI 과매수 ({rsi:.0f})")
    if high52w and high52w > 0:
        pos = price / high52w * 100
        if pos >= 95: reasons.append(f"52주 고점 근접 ({pos:.0f}%)")
    if fair_value and fair_value > 0:
        overpriced = (price - fair_value) / fair_value * 100
        if overpriced >= 15: reasons.append(f"적정가 대비 {overpriced:.0f}% 고평가")
    if fg_score is not None and fg_score >= 80: reasons.append(f"시장 극단적 탐욕 ({fg_score})")
    n = len(reasons)
    if n >= 3: return {"level": "경고", "reasons": reasons, "color": "#ff4060", "title": "🚫 고위험 구간"}
    elif n >= 2: return {"level": "주의", "reasons": reasons, "color": "#ffc940", "title": "⚠️ 단기 과열"}
    # 52주 고점 95%↑는 단독으로도 '주의' (물림 위험이 본질적으로 큼)
    elif n == 1 and high52w and high52w > 0 and price / high52w * 100 >= 95:
        return {"level": "주의", "reasons": reasons, "color": "#ffc940", "title": "⚠️ 52주 고점권"}
    else: return {"level": "none", "reasons": reasons, "color": "", "title": ""}

def price_targets(price, op, rsi, pivot):
    if op == "중립": return {"sp": 0, "sl": "해당없음", "tp": 0, "tp2": 0, "stop": 0}
    if op == "매수":
        tp1  = round(price * (1.12 if rsi and rsi < 35 else 1.08))
        tp2  = round(pivot["r2"]) if pivot else round(price * 1.15)
        stop = round(pivot["s1"]) if pivot else round(price * 0.94)
        return {"sp": price, "sl": "매수 추천가", "tp": tp1, "tp2": tp2, "stop": stop}
    return {"sp": round(price * 1.02), "sl": "매도 추천가",
            "tp": round(price * 0.92), "tp2": round(price * 0.88), "stop": round(price * 1.05)}

def gen_text(code, op, rsi, wr, mfi, ft, obv, weekly, investor, short, vol_surge, breakout):
    f   = investor.get("foreign", 0) if investor else 0
    inst = investor.get("institution", 0) if investor else 0
    basis = [
        f"RSI {rsi or '-'} · Williams%R {wr or '-'} · MFI {mfi or '-'} — "
        f"{'다중 과매도' if (rsi and rsi < 30) or (wr and wr < -80) else '다중 과매수 주의' if (rsi and rsi > 70) or (wr and wr > -20) else '지표 중립권'}",
        f"외국인 {'+' if f > 0 else ''}{f:,}주 · 기관 {'+' if inst > 0 else ''}{inst:,}주 · OBV {obv['trend'] if obv else '-'}",
        f"주봉 {weekly['opinion']} · 공매도 {short.get('ratio', 0)}% · "
        f"{'52주 신고가 근접' if breakout.get('nearHigh') else '52주 신저가 근접' if breakout.get('nearLow') else str(breakout.get('position', 50)) + '%'}",
    ]
    notes = (
        ["피봇 S1 지지 확인 후 분할 매수", "OBV 수급 지속 확인", "주봉 신호 일치 시 비중 확대"] if op == "매수" else
        ["피봇 R1 저항 확인 후 분할 매도", "공매도 상승 시 매도 강화", "주봉 데드크로스 확인 후 매도"] if op == "매도" else
        ["주봉·일봉 동시 매수 신호 확인 후 진입", "외국인 순매수 전환 시 진입 검토", "공매도 감소 + 거래량 증가 조합 주시"]
    )
    return basis, [], notes


# ─── 종목 분석 메인 ─────────────────────────────────────────────────────────
def analyze_stock(stock, kospi, market=None):
    code, name = stock["code"], stock["name"]
    print(f"\n▶ {name} ({code}) 분석 중...", file=sys.stderr)

    is_real_kis = KIS_AVAILABLE and "openapivts" not in KIS_BASE_URL
    if is_real_kis:
        kis_price = fetch_kis_price(code)
        naver = kis_price or fetch_naver_price(code)
    else:
        naver = fetch_naver_price(code)
        if not naver and KIS_AVAILABLE:
            naver = fetch_kis_price(code)

    # 수급은 네이버 통합(KRX+NXT, marketType=ALL)을 우선 — KIS는 KRX만이라 NXT 누락됨.
    # 네이버는 5·20·60일 누적·일별 흐름·연속일수까지 풍부. 실패 시에만 KIS(KRX) 폴백.
    investor = fetch_naver_investor(code) or (fetch_kis_investor(code) if is_real_kis else None) or {
                           "foreign": 0, "institution": 0, "individual": 0,
                           "foreignTrend": "중립", "comment": "수급 데이터 없음"}
    kis_short = fetch_kis_short(code) if KIS_AVAILABLE else None
    short     = kis_short or fetch_short_selling(code) or {"ratio": 0, "volume": 0, "comment": "없음"}
    news = []
    dart = fetch_dart(code)
    time.sleep(0.1)

    meta_d, candles_d = fetch_yahoo_ohlcv(stock["yf"], "1d", "60d")
    meta_w, candles_w = fetch_yahoo_ohlcv(stock["yf"], "1wk", "1y")
    meta_m, candles_m = fetch_yahoo_ohlcv(stock["yf"], "1mo", "2y")

    closes_d  = [c["close"]  for c in candles_d]
    highs_d   = [c["high"]   for c in candles_d]
    lows_d    = [c["low"]    for c in candles_d]
    opens_d   = [c["open"]   for c in candles_d]
    volumes_d = [c["volume"] for c in candles_d]
    has_data  = len(closes_d) >= 10

    price   = naver["price"]     if naver else round(meta_d.get("regularMarketPrice", 0))
    prev    = naver["prevClose"]  if naver else round(meta_d.get("previousClose", 0))
    high52w = naver["high52w"]   if naver else round(meta_d.get("fiftyTwoWeekHigh", 0))
    low52w  = naver["low52w"]    if naver else round(meta_d.get("fiftyTwoWeekLow", 0))
    source  = naver["source"]    if naver else "Yahoo Finance"

    yf_high = round(meta_d.get("fiftyTwoWeekHigh", 0))
    yf_low  = round(meta_d.get("fiftyTwoWeekLow", 0))
    if high52w <= 0 or high52w < price: high52w = max(yf_high, price)
    if low52w <= 0 or low52w > price:   low52w = min(yf_low, price) if yf_low > 0 else low52w
    # API가 52주 저점을 못 주면 보유 일봉 데이터의 최저가로 보정 (0 방지)
    if (not low52w or low52w <= 0) and has_data and lows_d:
        low52w = round(min(lows_d))
    if (not high52w or high52w <= 0) and has_data and highs_d:
        high52w = round(max(highs_d))

    if price == 0: return None

    fin = fetch_financial_data(stock["yf"], code)
    fair = calc_fair_value(code, price, fin)
    fundamentals = fetch_fundamentals(code)
    eps = fin.get("eps") or 0
    bps = fin.get("bps") or 0

    rsi   = calc_rsi(closes_d)   if has_data else None
    macd, macd_sig, macd_hist = calc_macd(closes_d) if has_data else (None, None, None)
    stoch = calc_stoch(highs_d, lows_d, closes_d) if has_data else None
    stoch_rsi  = calc_stoch_rsi(closes_d) if has_data else None
    divergence = detect_divergence(closes_d, highs_d, lows_d) if has_data else None
    ichimoku   = calc_ichimoku(highs_d, lows_d, closes_d) if has_data else None
    cci        = calc_cci(highs_d, lows_d, closes_d) if has_data else None
    psar       = calc_psar(highs_d, lows_d, closes_d) if has_data else None
    value_surge = calc_value_surge(closes_d, volumes_d) if has_data else None
    wr    = calc_williams(highs_d, lows_d, closes_d) if has_data else None
    adx   = calc_adx(highs_d, lows_d, closes_d) if has_data else None
    mfi   = calc_mfi(highs_d, lows_d, closes_d, volumes_d) if has_data else None
    obv   = calc_obv(closes_d, volumes_d) if has_data else {"obv": 0, "slope": 0, "trend": "횡보"}
    vwap  = calc_vwap(highs_d, lows_d, closes_d, volumes_d) if has_data else None
    pivot = calc_pivot(highs_d, lows_d, closes_d) if has_data else None
    sma5  = calc_sma(closes_d, 5)  if has_data else None
    sma20 = calc_sma(closes_d, 20) if has_data else None
    sma60 = calc_sma(closes_d, 60) if has_data else None
    pats  = detect_patterns(opens_d, highs_d, lows_d, closes_d) if has_data else []

    weekly  = calc_weekly_signal(candles_w)
    monthly = calc_weekly_signal(candles_m); monthly["timeframe"] = "월봉"
    rs      = calc_relative_strength(
        round((price - prev) / prev * 100, 2) if prev else 0, kospi.get("changePct", 0))
    breakout  = check_52w_breakout(price, high52w, low52w)
    vol_surge = check_volume_surge(volumes_d) if has_data else {"surge": False, "ratio": 1.0, "comment": ""}

    avg_vol   = sum(volumes_d[-20:]) / min(20, len(volumes_d)) if has_data and volumes_d else 0
    vol_ratio = volumes_d[-1] / avg_vol if avg_vol > 0 else 1
    fg = fear_greed(rsi, stoch, obv["slope"] if obv else 0, adx["adx"] if adx else None, vol_ratio)

    ft = investor.get("foreignTrend", "중립") if investor else "중립"
    contra = contra_signal(rsi, macd, macd_sig, obv, pats, fg["score"], investor)

    boll_result = calc_boll(closes_d) if has_data else None
    atr = calc_atr(highs_d, lows_d, closes_d) if has_data else None
    boll_pos = None
    if boll_result and boll_result["upper"] != boll_result["lower"]:
        boll_pos = round((closes_d[-1] - boll_result["lower"]) / (boll_result["upper"] - boll_result["lower"]) * 100)
        if boll_pos is not None:
            boll_result["position"] = boll_pos

    opinion, score, nuance = master_signal(
        rsi, macd, macd_sig, stoch, wr, mfi, adx, obv,
        closes_d, price, high52w, low52w, vwap,
        weekly["opinion"], investor, short.get("ratio", 0), news,
        stoch_rsi, divergence, ichimoku, cci, psar, value_surge,
        boll_data=boll_result, weekly_rsi=weekly.get("rsi"), patterns=pats,
        fx_price=(market.get("fx", {}).get("price", 0) if market else 0)
    )

    # ── 역발상 반등 감지 ──────────────────────────────
    # 매도 우위인데 과매도 신호 3개 이상 겹치면 반등 가능성 신호
    contrarian = ""
    if score < 0:
        oversold_signals = []
        if stoch_rsi and stoch_rsi.get("k", 50) < 20:
            oversold_signals.append("스토캐스틱 RSI 과매도")
        if rsi and rsi < 40:
            oversold_signals.append("RSI 저점")
        if boll_result and boll_result.get("position", 50) < 25:
            oversold_signals.append("볼린저 하단")
        if wr and wr < -70:
            oversold_signals.append("Williams %R 과매도")
        if stoch and stoch < 20:
            oversold_signals.append("스토캐스틱 과매도")
        if high52w > 0 and low52w > 0 and high52w > low52w:
            pos52 = (price - low52w) / (high52w - low52w) * 100
            if pos52 < 25:
                oversold_signals.append("52주 저점 근처")
        if boll_result:
            s20 = calc_sma(closes_d, 20)
            if s20 and (price - s20) / s20 * 100 < -8:
                oversold_signals.append("이격도 과대")
        if len(oversold_signals) >= 3:
            score += 2
            contrarian = "⚡ 역발상 반등 주목 — " + " · ".join(oversold_signals[:3])
            if score >= 6:   opinion = "매수"
            elif score >= 0: opinion = "중립"
            # nuance 재계산
            if opinion == "중립":
                if   score >= 4:  nuance = "매수 우위 (문턱 근접)"
                elif score >= 1:  nuance = "약한 매수 우위"
                elif score == 0:  nuance = "완전 중립 (관망)"
                elif score >= -2: nuance = "약한 매도 우위"
                else:             nuance = "매도 우위 (문턱 근접)"
    # ──────────────────────────────────────────────────
    # ── 반대매매 위험 감지 ────────────────────────────
    # 개인 연속 순매도 + 거래량 급증 + 장대음봉 조합
    margin_call_risk = ""
    if investor:
        daily = investor.get("daily", [])
        indiv = investor.get("individual", 0) or 0
        # 개인 최근 3일 연속 순매도 여부
        recent_indiv_sell = False
        if len(daily) >= 3:
            # daily는 외국인 기준이라 개인 daily가 별도로 없으면 당일만 체크
            pass
        # 조건 체크
        risk_signals = []
        if indiv < 0:
            risk_signals.append("개인 순매도")
        if vol_surge and vol_surge.get("surge"):
            risk_signals.append("거래량 급증")
        chg_pct = round((price - prev) / prev * 100, 2) if prev and prev > 0 else 0
        if chg_pct < -3:
            risk_signals.append(f"급락({chg_pct:.1f}%)")
        has_bearish_candle = any(p.get("type") in ["강한하락","하락반전"] for p in pats)
        if has_bearish_candle:
            risk_signals.append("장대음봉")
        if len(risk_signals) >= 3:
            margin_call_risk = "⚠️ 반대매매 주의 — " + " · ".join(risk_signals[:3])
            print(f"  ⚠️ 반대매매 주의 ({code}): {margin_call_risk}", file=sys.stderr)
        elif len(risk_signals) > 0:
            print(f"  📋 반대매매 신호 ({code}): {risk_signals} ({len(risk_signals)}개 — 기준 미달)", file=sys.stderr)

    # ── 물갈이 감지 ───────────────────────────────────
    # 개인 대량 매도 + 외국인·기관 동시 매수 + 주가가 충분히 빠진 자리
    turnover = ""
    if investor:
        indiv   = investor.get("individual", 0) or 0
        foreign = investor.get("foreign", 0) or 0
        inst    = investor.get("institution", 0) or 0
        indiv5  = investor.get("indiv5", 0) or 0
        # 52주 위치 60% 미만 또는 SMA20 아래
        pos52 = breakout.get("position", 100) if breakout else 100
        below_avg = (price < calc_sma(closes_d, 20)) if closes_d and len(closes_d) >= 20 else False
        price_depressed = pos52 < 60 or below_avg
        # 조건 체크
        indiv_heavy_sell = indiv < 0 and (abs(indiv) > abs(foreign) or abs(indiv) > abs(inst))
        indiv5_sell = indiv5 < 0
        both_buying = foreign > 0 and inst > 0
        if indiv_heavy_sell and indiv5_sell and both_buying and price_depressed:
            turnover = "🔄 물갈이 진행 — 개인 투매를 외국인·기관이 흡수 중"
            print(f"  🔄 물갈이 감지 ({code}): 개인 {indiv:+,} / 외국인 {foreign:+,} / 기관 {inst:+,} / 52주위치 {pos52}%", file=sys.stderr)
    # ──────────────────────────────────────────────────
    # ──────────────────────────────────────────────────
    # 시장 분위기 브레이크: 전일 밤 미국 선행지표가 비우호적이면 매수 신호를 보수적으로
    market_brake = ""
    if market and opinion == "매수":
        if market.get("summary", {}).get("mood") == "adverse":
            score -= 2
            if score < 6:
                opinion = "중립"
                market_brake = "시장 비우호적 — 매수 신호 보류"
                nuance = "매수 우위 (문턱 근접)" if score >= 4 else "약한 매수 우위"
            else:
                market_brake = "시장 비우호적 — 신중 진입 권장"
    cautious = assess_cautious_entry(opinion, score, ichimoku, stoch_rsi,
                                     divergence, psar, investor, cci, price, pivot)
    overheat = assess_overheat_warning(rsi, stoch_rsi, price, high52w,
                                       fair.get("fair_value", 0), fg["score"])

    # ── 추격매수 경고 ───────────────────────────────────
    # 당일 급등(+5%↑) + 52주 고점권(85%↑) + 거래량 잠김(0.8배 미만)
    # = 고점에서 매물 잠긴 채 오른 자리 → 추격 진입 시 물림 위험
    chase_warning = ""
    day_chg = round((price - prev) / prev * 100, 2) if prev and prev > 0 else 0
    pos52_now = breakout.get("position", 0) if breakout else 0
    if day_chg >= 5 and pos52_now >= 85 and vol_ratio < 0.8:
        chase_warning = "🚫 추격 위험 — 고점권 급등 + 거래량 잠김. 눌림 기다리세요"
        print(f"  🚫 추격매수 경고 ({code}): +{day_chg}% / 52주 {pos52_now}% / 거래량 {vol_ratio:.2f}배", file=sys.stderr)
    # ──────────────────────────────────────────────────

    pt = price_targets(price, opinion, rsi or 50, pivot)
    basis, risk, notes = gen_text(code, opinion, rsi, wr, mfi, ft, obv,
                                  weekly, investor, short, vol_surge, breakout)

    # 신호 × 외국인 수급(5일 흐름) 조합 해설 — 해석 + 제안 한 줄씩
    flow_read = ""
    f5 = investor.get("foreign5", 0) if investor else 0
    fdir = "매수" if f5 > 0 else "매도" if f5 < 0 else "중립"
    if contrarian:
        flow_read = "매도 우위지만 과매도 신호가 집중돼 단기 반등 가능성이 있어요. │ 소량 분할 진입 고려, 손절선 꼭 확인."
    elif opinion == "매수" and fdir == "매수":
        flow_read = "신호·외국인 수급 모두 매수 우위로 방향이 일치해요. │ 신뢰도 높은 편, 지지구간 분할매수로 접근."
    elif opinion == "매수" and fdir == "매도":
        flow_read = "기술적으론 매수 신호지만 외국인은 5일째 이탈 중이에요. │ 주가 상승의 지속성이 의심되니 추격 말고 보수적으로."
    elif opinion == "매도" and fdir == "매수":
        flow_read = "단기 지표는 과열·조정 신호지만 외국인은 매집 중이에요. │ 큰 흐름은 살아있으니, 추격 대신 조정 시 지지구간에서 노려볼 만."
    elif opinion == "매도" and fdir == "매도":
        flow_read = "신호·외국인 수급 모두 약세예요. │ 진입은 자제하고 관망이 안전."
    elif fdir == "매수":
        flow_read = "신호는 중립이나 외국인은 5일째 매집 중이에요. │ 수급은 우호적, 지지구간 확인하며 분할 접근."
    elif fdir == "매도":
        flow_read = "신호는 중립이고 외국인은 이탈 중이에요. │ 서두르지 말고 수급 방향 전환을 확인 후 대응."

    # ── 차익실현성 급락 감지 ──
    # 펀더멘털·추세 멀쩡한데 단기 급락 → 패닉매도 아닌 차익실현 가능성
    # 단, 누가 받는지가 핵심: 기관이 받으면 차익실현 / 개인만 받고 외국인·기관 동반이탈이면 진짜하락 경고
    profit_taking = ""
    today_pct = round((price - prev) / prev * 100, 2) if prev else 0
    if today_pct <= -3:  # 당일 -3% 이상 급락
        fund_ok = fundamentals and "건전" in fundamentals.get("grade", "")
        trend_ok = (ichimoku and ichimoku.get("signal") == "매수") or \
                   (adx and adx.get("adx", 0) >= 25)
        f_val = (investor.get("foreign", 0) or 0) if investor else 0
        i_val = (investor.get("institution", 0) or 0) if investor else 0
        p_val = (investor.get("individual", 0) or 0) if investor else 0
        inst_buy = i_val > 0
        smart_dumping = f_val < 0 and i_val < 0   # 외국인+기관 동반 이탈
        indiv_only = p_val > 0 and smart_dumping  # 개인만 받음
        if indiv_only:
            # 개인만 받고 스마트머니 동반이탈 = 진짜 하락 위험. 차익실현으로 보면 안 됨
            profit_taking = (f"⚠️ {today_pct}% 급락 — 외국인·기관 동반 대량매도, 개인만 받는 중. "
                             f"실적은 받쳐줘도 스마트머니 이탈은 경계 신호예요. "
                             f"섣부른 저가매수보다 외국인 수급 돌아오는지 확인 후 대응.")
        elif fund_ok and trend_ok and inst_buy:
            # 기관이 받으면 차익실현 가능성
            profit_taking = (f"💡 차익실현성 급락 가능성 — 실적 건전 + 추세 유지 중인데 "
                             f"{today_pct}% 급락, 기관이 받는 중. 펀더멘털 훼손보다 차익실현 매물일 수 있어요. "
                             f"패닉 매도보다 과매도 반등 주목 (단, 추세 꺾이면 손절).")
        elif fund_ok and trend_ok:
            profit_taking = (f"💡 {today_pct}% 급락이나 실적·추세는 유지 중 — 차익실현 매물일 수 있어요. "
                             f"다만 받아주는 수급이 약하니 반등은 확인 후 대응.")

    def cmt_rsi(v):
        if v is None: return "데이터 부족"
        return ("강한 과매도" if v < 30 else "저점권" if v < 45 else
                "강한 과매수" if v > 70 else "과매수 진입" if v > 60 else "중립")

    return {
        "code": code, "price": price,
        "change": price - prev,
        "changePct": round((price - prev) / prev * 100, 2) if prev else 0,
        "high52w": high52w, "low52w": low52w,
        "opinion": opinion, "score": score, "source": source,
        "nuance": nuance,
        "contrarian": contrarian,
        "profitTaking": profit_taking,
        "marginCallRisk": margin_call_risk,
        "turnover": turnover,
        "chaseWarning": chase_warning,
        "marketBrake": market_brake,
        "flowRead": flow_read,
        "tradedAt": naver.get("tradedAt", "") if naver else "",
        "fairValue": fair.get("fair_value", 0),
        "fairValueGap": fair.get("gap", 0),
        "fairValueComment": fair.get("gap_comment", ""),
        "fairValueDetail": {
            "basis": fair.get("basis", ""),
            "target_price": fair.get("target_price", 0),
            "current_per": fair.get("current_per", 0),
            "cns_per": fair.get("cns_per", 0),
            "recomm": fair.get("recomm", 0),
            "sector": fair.get("sector", ""),
            "sector_per": fair.get("sector_per", 0),
        },
        "eps": eps or 0, "bps": bps or 0,
        "boll": {"upper": round(boll_result["upper"]) if boll_result else 0,
                 "mid":   round(boll_result["mid"])   if boll_result else 0,
                 "lower": round(boll_result["lower"]) if boll_result else 0,
                 "position": boll_pos} if boll_result else None,
        "atr": atr,
        "atrStopLoss": round(price - atr["atr"] * 2) if atr else 0,
        "suggestedPrice": pt["sp"], "suggestedLabel": pt["sl"],
        "targetPrice": pt["tp"], "targetPrice2": pt["tp2"], "stopLoss": pt["stop"],
        "rsi": rsi, "rsiComment": cmt_rsi(rsi),
        "macd": macd or 0, "macdSignal": macd_sig or 0, "macdHist": macd_hist or 0,
        "macdComment": ("데이터 부족" if macd is None else
                        "골든크로스 — 상승 모멘텀" if macd > macd_sig else "데드크로스 — 하락 압력"),
        "stoch": stoch or 50,
        "stochComment": ("데이터 부족" if stoch is None else
                         "과매도 반등 임박" if stoch < 20 else "과매수 조정 주의" if stoch > 80 else "중립"),
        "stochRsi": stoch_rsi, "divergence": divergence,
        "ichimoku": ichimoku, "cci": cci, "psar": psar, "valueSurge": value_surge,
        "cautiousEntry": cautious, "overheat": overheat,
        "wr": wr, "wrComment": ("데이터 부족" if wr is None else
                                "과매도" if wr < -80 else "과매수" if wr > -20 else "중립"),
        "mfi": mfi, "mfiComment": ("데이터 부족" if mfi is None else
                                   "거래량 기반 과매도" if mfi < 20 else "거래량 기반 과매수" if mfi > 80 else "중립"),
        "adx": adx, "obv": obv, "vwap": vwap, "pivot": pivot,
        "sma5": sma5, "sma20": sma20, "sma60": sma60,
        "patterns": pats, "contra": contra, "fg": fg,
        "ft": ft, "fc": investor.get("comment", "") if investor else "",
        "volRatio": round(vol_ratio, 2),
        "investor": investor, "short": short,
        "fundamentals": fundamentals,
        "creditRatio": round(meta_d.get("creditRatio", 0), 2),
        "weekly": weekly, "monthly": monthly,
        "relativeStrength": rs, "breakout": breakout, "volSurge": vol_surge,
        "news": news, "dart": dart,
        "basis": basis, "risk": [], "notes": notes,
        "noChart": not has_data,
    }


def main():
    now = datetime.now(KST)
    print(f"📊 수집 시작: {now.strftime('%Y-%m-%d %H:%M:%S KST')}", file=sys.stderr)
    kospi = fetch_kospi()
    print(f"  KOSPI: {kospi['price']} ({'+' if kospi['changePct'] >= 0 else ''}{kospi['changePct']}%)", file=sys.stderr)
    market = fetch_market_signal()
    nxt_prices = fetch_nxt_prices()  # NXT 애프터마켓 실시간 (거래중일 때만 유효)

    stocks_data = []
    for stock in STOCKS:
        try:
            result = analyze_stock(stock, kospi, market)
            if result:
                # NXT 실시간 가격 부착 (지지가 도달·낙폭 표시용)
                nxt = nxt_prices.get(stock["code"])
                if nxt and nxt.get("status") == "OPEN":
                    result["nxt"] = nxt
                stocks_data.append(result)
            else: print(f"  ⚠ {stock['name']} 데이터 없음", file=sys.stderr)
        except Exception as e:
            print(f"  ❌ {stock['name']} 오류: {e}", file=sys.stderr)
        time.sleep(0.2)

    output = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "updatedTime": now.strftime("%H:%M"),
        "kospi": kospi,
        "market": market,
        "stocks": stocks_data,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ data.json 완료 — {len(stocks_data)}개 종목", file=sys.stderr)

if __name__ == "__main__":
    main()
