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
    종합 점수가 음(-)이면 매수 신호를 보수적으로 누르는 브레이크로 활용."""
    targets = [
        ("^SOX",  "필라델피아 반도체", "sox"),
        ("NQ=F",  "나스닥 선물",       "nasdaq"),
        ("KRW=X", "원/달러 환율",      "fx"),
    ]
    out = {}
    for sym, name, key in targets:
        enc = quote(sym, safe="")  # ^SOX, NQ=F 등 특수문자 인코딩
        for base in ["query1", "query2"]:
            try:
                d = http_json(f"https://{base}.finance.yahoo.com/v8/finance/chart/{enc}?interval=1d&range=5d", timeout=5)
                meta = d.get("chart", {}).get("result", [{}])[0].get("meta", {})
                cur  = meta.get("regularMarketPrice") or 0
                prev = meta.get("chartPreviousClose") or meta.get("previousClose") or 0
                if cur and prev:
                    pct = round((cur - prev) / prev * 100, 2)
                    out[key] = {"name": name, "price": round(cur, 2), "pct": pct}
                    break
            except Exception as e:
                print(f"  시장지표 실패 ({sym}/{base}): {e}", file=sys.stderr)

    # 종합 점수: SOX·나스닥 상승=우호(+), 환율 상승(원화 약세)=비우호(-)
    score = 0.0
    if "sox"    in out: score += out["sox"]["pct"]    * 1.5
    if "nasdaq" in out: score += out["nasdaq"]["pct"] * 1.0
    if "fx"     in out: score -= out["fx"]["pct"]     * 1.0

    if   score >=  1.5: mood, label = "favorable", "우호적"
    elif score <= -1.5: mood, label = "adverse",   "비우호적"
    else:               mood, label = "neutral",   "중립"

    # 행동 제안형 한두 줄 해설 (지표 조합에 따라 자동 선택)
    fx_pct  = out.get("fx", {}).get("pct", 0)      # 환율 +면 원화 약세(악재)
    sox_pct = out.get("sox", {}).get("pct", 0)
    fx_spike = fx_pct >= 1.0                         # 원/달러 1%+ 급등
    if mood == "adverse":
        if fx_spike:
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
    parts = []
    for k in ("sox", "nasdaq", "fx"):
        if k in out:
            parts.append(f"{out[k]['name']} {'+' if out[k]['pct']>=0 else ''}{out[k]['pct']}%")
    print(f"  🌐 시장 분위기: {label} (score {round(score,1)}) — {' / '.join(parts)}", file=sys.stderr)
    return out


# ─── 적정주가 ──────────────────────────────────────────────────────────────
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
    if kospi_pct == 0: return {"rs": 0, "comment": "KOSPI 데이터 없음", "strong": False}
    rs = round(stock_pct - kospi_pct, 2); strong = rs > 0
    comment = (f"KOSPI 대비 +{rs}%p 강세" if rs > 1 else
               f"KOSPI 대비 {rs}%p 약세" if rs < -1 else "KOSPI 대비 중립")
    return {"rs": rs, "comment": comment, "strong": strong}

def check_52w_breakout(price, high52w, low52w):
    if high52w == 0: return {"nearHigh": False, "nearLow": False, "position": 50, "comment": ""}
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
                  ichimoku=None, cci=None, psar=None, value_surge=None):
    score = 0
    if rsi: score += 2 if rsi < 30 else 1 if rsi < 45 else -2 if rsi > 70 else -1 if rsi > 60 else 0
    if macd and macd_sig: score += 2 if macd > macd_sig else -2
    if wr: score += 1 if wr < -80 else -1 if wr > -20 else 0
    if stoch: score += 1 if stoch < 20 else -1 if stoch > 80 else 0
    if mfi: score += 1 if mfi < 20 else -1 if mfi > 80 else 0
    if obv: score += 1 if obv["slope"] > 2 else -1 if obv["slope"] < -2 else 0
    if adx and adx["adx"] < 15: score = round(score * 0.7)
    if stoch_rsi:
        if stoch_rsi["signal"] == "매수": score += 2
        elif stoch_rsi["signal"] == "매도": score -= 2
    if divergence:
        if divergence.get("bullish"): score += 3
        if divergence.get("bearish"): score -= 3
    if ichimoku:
        if ichimoku["signal"] == "매수": score += 2 if "강한" in ichimoku["comment"] else 1
        elif ichimoku["signal"] == "매도": score -= 2 if "강한" in ichimoku["comment"] else 1
    if cci:
        if cci["signal"] == "매수": score += 2 if "극단" in cci["comment"] else 1
        elif cci["signal"] == "매도": score -= 2 if "극단" in cci["comment"] else 1
    if psar:
        if psar["signal"] == "매수": score += 1
        elif psar["signal"] == "매도": score -= 1
    if value_surge and value_surge["surge"]:
        score += 1 if rsi and rsi > 50 else -1 if rsi and rsi < 50 else 0
    if h52 > 0 and l52 > 0:
        pos = (price - l52) / (h52 - l52) * 100
        score += 1 if pos < 20 else -1 if pos > 85 else 0
    if vwap: score += 1 if price > vwap else -1
    s5, s20 = calc_sma(closes, 5), calc_sma(closes, 20)
    if s5 and s20: score += 1 if s5 > s20 else -1
    if weekly_opinion == "매수": score += 2
    elif weekly_opinion == "매도": score -= 2
    if short_ratio > 0:
        if short_ratio > 5: score -= 1
        elif short_ratio < 1: score += 1
    if news_list:
        pos_count = len([n for n in news_list if n["sentiment"] == "긍정"])
        neg_count = len([n for n in news_list if n["sentiment"] == "부정"])
        if pos_count > neg_count: score += 1
        elif neg_count > pos_count: score -= 1
    opinion = "매수" if score >= 6 else "매도" if score <= -5 else "중립"
    return opinion, score

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

    kis_inv  = fetch_kis_investor(code) if is_real_kis else None
    investor = kis_inv or {"foreign": 0, "institution": 0, "individual": 0,
                           "foreignTrend": "중립", "comment": "수급은 네이버·증권사 앱에서 직접 확인"}
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

    if price == 0: return None

    fin = fetch_financial_data(stock["yf"], code)
    fair = calc_fair_value(code, price, fin)
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
    opinion, score = master_signal(
        rsi, macd, macd_sig, stoch, wr, mfi, adx, obv,
        closes_d, price, high52w, low52w, vwap,
        weekly["opinion"], investor, short.get("ratio", 0), news,
        stoch_rsi, divergence, ichimoku, cci, psar, value_surge
    )
    # 시장 분위기 브레이크: 전일 밤 미국 선행지표가 비우호적이면 매수 신호를 보수적으로
    market_brake = ""
    if market and opinion == "매수":
        if market.get("summary", {}).get("mood") == "adverse":
            score -= 2
            if score < 6:
                opinion = "중립"
                market_brake = "시장 비우호적 — 매수 신호 보류"
            else:
                market_brake = "시장 비우호적 — 신중 진입 권장"
    cautious = assess_cautious_entry(opinion, score, ichimoku, stoch_rsi,
                                     divergence, psar, investor, cci, price, pivot)
    overheat = assess_overheat_warning(rsi, stoch_rsi, price, high52w,
                                       fair.get("fair_value", 0), fg["score"])
    pt = price_targets(price, opinion, rsi or 50, pivot)
    basis, risk, notes = gen_text(code, opinion, rsi, wr, mfi, ft, obv,
                                  weekly, investor, short, vol_surge, breakout)

    def cmt_rsi(v):
        if v is None: return "데이터 부족"
        return ("강한 과매도" if v < 30 else "저점권" if v < 45 else
                "강한 과매수" if v > 70 else "과매수 진입" if v > 60 else "중립")

    boll = calc_boll(closes_d) if has_data else None
    boll_pos = None
    if boll and boll["upper"] != boll["lower"]:
        boll_pos = round((closes_d[-1] - boll["lower"]) / (boll["upper"] - boll["lower"]) * 100)

    return {
        "code": code, "price": price,
        "change": price - prev,
        "changePct": round((price - prev) / prev * 100, 2) if prev else 0,
        "high52w": high52w, "low52w": low52w,
        "opinion": opinion, "score": score, "source": source,
        "marketBrake": market_brake,
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
        "boll": {"upper": round(boll["upper"]) if boll else 0,
                 "mid":   round(boll["mid"])   if boll else 0,
                 "lower": round(boll["lower"]) if boll else 0,
                 "position": boll_pos} if boll else None,
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

    stocks_data = []
    for stock in STOCKS:
        try:
            result = analyze_stock(stock, kospi, market)
            if result: stocks_data.append(result)
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
