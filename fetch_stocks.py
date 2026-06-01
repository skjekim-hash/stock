#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
고도화된 국내 주식 데이터 수집 + 분석 스크립트
- 외국인·기관 순매수
- 공매도 비율
- KOSPI 대비 상대강도
- 멀티 타임프레임 (주봉)
- 52주 신고가 근접
- 종목 뉴스 수집 (네이버)
- DART 공시 수집
- 거래량 급증 감지
"""

import json, sys, re, time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlencode, quote

# 수급 데이터는 KIS API(실전) 또는 네이버 금융에서 수집
PYKRX_AVAILABLE = False

# KIS API 설정 (GitHub Secrets에서 환경변수로 주입)
import os
KIS_APP_KEY    = os.environ.get("KIS_APP_KEY", "")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET", "")
DART_API_KEY   = os.environ.get("DART_API_KEY", "")
KIS_AVAILABLE  = bool(KIS_APP_KEY and KIS_APP_SECRET)
KIS_BASE_URL   = ("https://openapi.koreainvestment.com:9443"      # 실전투자
                  if os.environ.get("KIS_REAL", "").lower() in ("1", "true", "yes")
                  else "https://openapivts.koreainvestment.com:29443")  # 모의투자(기본)
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
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def http_json(url, timeout=15, headers=None):
    return json.loads(http_get(url, timeout, headers))


def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        print(f"  ⚠ {fn.__name__ if hasattr(fn,'__name__') else '?'}: {e}", file=sys.stderr)
        return default


def to_n(v, default=0):
    if v is None:
        return default
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────
# KIS API (모의투자 - 실시간 주가·수급·공매도)
# ─────────────────────────────────────────
def kis_get_token():
    """KIS 접근토큰 발급 (캐시)"""
    global KIS_TOKEN
    if not KIS_AVAILABLE:
        print("  KIS 비활성 — Secrets 미설정", file=sys.stderr)
        return ""
    if KIS_TOKEN["access_token"] and time.time() < KIS_TOKEN["expires"]:
        return KIS_TOKEN["access_token"]
    print(f"  KIS 토큰 요청... KEY={KIS_APP_KEY[:8] if KIS_APP_KEY else 'EMPTY'}", file=sys.stderr)
    try:
        url = f"{KIS_BASE_URL}/oauth2/tokenP"
        body = json.dumps({
            "grant_type": "client_credentials",
            "appkey": KIS_APP_KEY,
            "appsecret": KIS_APP_SECRET,
        }).encode()
        req = Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json"
        })
        with urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        token = d.get("access_token", "")
        if token:
            KIS_TOKEN["access_token"] = token
            KIS_TOKEN["expires"] = time.time() + 3600 * 23
            print("  ✅ KIS 토큰 발급 성공", file=sys.stderr)
            return token
    except Exception as e:
        print(f"  KIS 토큰 실패: {e}", file=sys.stderr)
    return ""


def kis_request(path, params, tr_id):
    """KIS API GET 요청"""
    token = kis_get_token()
    if not token:
        return None
    from urllib.parse import urlencode
    url = f"{KIS_BASE_URL}{path}?{urlencode(params)}"
    req = Request(url, headers={
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  KIS 요청 실패 ({tr_id}): {e}", file=sys.stderr)
    return None


def fetch_kis_price(code):
    """KIS 실시간 주가 (통합시세 - 넥스트레이드 포함)"""
    if not KIS_AVAILABLE:
        return None
    try:
        d = kis_request(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            "FHKST01010100"
        )
        if not d:
            return None
        out = d.get("output", {})
        price = to_n(out.get("stck_prpr") or 0)
        prev  = to_n(out.get("stck_sdpr") or 0)  # 기준가(전일 종가)
        if price > 0:
            # 52주 고저는 w52_ 필드 사용 (stck_hgpr/lwpr는 당일 고저라 틀림)
            high52 = to_n(out.get("w52_hgpr") or 0)
            low52  = to_n(out.get("w52_lwpr") or 0)
            change = to_n(out.get("prdy_vrss") or 0)        # 전일 대비
            sign   = out.get("prdy_vrss_sign") or "3"        # 1상한2상승3보합4하한5하락
            if sign in ("4", "5"):
                change = -abs(change)
            return {
                "price":    round(price),
                "prevClose": round(prev) if prev else round(price - change),
                "change":   round(change),
                "changePct": round(to_n(out.get("prdy_ctrt") or 0), 2) * (-1 if sign in ("4","5") else 1),
                "high52w":  round(high52),
                "low52w":   round(low52),
                "tradedAt": out.get("stck_bsop_date", ""),
                "source":   "KIS API (통합시세)",
            }
    except Exception as e:
        print(f"  KIS 주가 실패 ({code}): {e}", file=sys.stderr)
    return None


def fetch_kis_investor(code):
    """KIS 당일 외국인·기관 순매수 (실전투자만 지원)"""
    if not KIS_AVAILABLE:
        return None
    # 모의투자 서버는 투자자별 조회 미지원 → 스킵
    if "openapivts" in KIS_BASE_URL:
        return None
    try:
        d = kis_request(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            "FHKST01010900"
        )
        if not d:
            return None
        out = d.get("output", {})
        f    = round(to_n(out.get("frgn_ntby_qty") or out.get("frgn_seln_vol") or 0))
        inst = round(to_n(out.get("orgn_ntby_qty") or out.get("inst_ntby_vol") or 0))
        indv = round(to_n(out.get("indvdl_ntby_qty") or 0))

        if f > 0 and inst > 0:
            trend = "매수우세"
            comment = f"외국인 +{f:,}주 · 기관 +{inst:,}주 동반 순매수 (당일)"
        elif f > 0:
            trend = "매수우세"
            comment = f"외국인 +{f:,}주 순매수 (당일)"
        elif f < 0 and inst < 0:
            trend = "매도우세"
            comment = f"외국인 {f:,}주 · 기관 {inst:,}주 동반 순매도 (당일)"
        elif f < 0:
            trend = "매도우세"
            comment = f"외국인 {f:,}주 순매도 (당일)"
        else:
            trend = "중립"
            comment = "외국인·기관 수급 중립 (당일)"

        return {
            "foreign": f, "institution": inst, "individual": indv,
            "foreignTrend": trend, "comment": comment, "date": "당일"
        }
    except Exception as e:
        print(f"  KIS 수급 실패 ({code}): {e}", file=sys.stderr)
    return None


def fetch_kis_short(code):
    """KIS 공매도 비율 (실전투자만 지원)"""
    if not KIS_AVAILABLE:
        return None
    if "openapivts" in KIS_BASE_URL:
        return None
    try:
        d = kis_request(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
             "FID_INPUT_DATE_1": datetime.now(KST).strftime("%Y%m%d"),
             "FID_INPUT_DATE_2": datetime.now(KST).strftime("%Y%m%d"),
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
            "FHKST03010100"
        )
        if d:
            out = d.get("output2", [{}])
            if out:
                ratio = to_n(out[0].get("short_sell_rate") or 0)
                if ratio > 0:
                    comment = ("공매도 비율 높음 — 하락 압력 주의" if ratio > 5 else
                               "공매도 비율 보통" if ratio > 2 else "공매도 비율 낮음")
                    return {"ratio": round(ratio, 2), "volume": 0, "comment": comment}
    except Exception as e:
        print(f"  KIS 공매도 실패 ({code}): {e}", file=sys.stderr)
    return None


# ─────────────────────────────────────────
# 네이버 금융 현재가
# ─────────────────────────────────────────
def fetch_naver_price(code):
    # 방법 1: /price 엔드포인트 - 최신 체결가 (가장 정확)
    try:
        d = http_json(f"https://m.stock.naver.com/api/stock/{code}/price?pageSize=2&page=1")
        rows = d if isinstance(d, list) else d.get("priceInfos") or d.get("prices") or []
        if rows and len(rows) > 0:
            latest = rows[0]
            price = to_n(latest.get("closePrice") or latest.get("nv") or 0)
            traded_at = latest.get("localTradedAt") or latest.get("tradeTime") or ""
            if price > 0:
                # basic에서 전일종가·52주 가져오기
                prev = high52w = low52w = 0
                try:
                    b = http_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
                    chg = to_n(b.get("compareToPreviousClosePrice", 0))
                    prev = round(price - chg) if chg else round(price)
                    high52w = round(to_n(b.get("highPrice")) or to_n(b.get("yearHighPrice")))
                    low52w  = round(to_n(b.get("lowPrice"))  or to_n(b.get("yearLowPrice")))
                except: pass
                return {
                    "price": round(price), "prevClose": prev,
                    "high52w": high52w, "low52w": low52w,
                    "tradedAt": str(traded_at)[:19],
                    "source": "네이버 금융",
                }
    except Exception as e:
        print(f"  네이버 price 실패 ({code}): {e}", file=sys.stderr)

    # 방법 2: /basic 엔드포인트 폴백
    try:
        d = http_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
        # 장중 실시간 우선: dealTradeTime, overMarketPriceInfo 등 확인
        price = (to_n(d.get("closePrice")) or to_n(d.get("currentPrice"))
                 or to_n(d.get("nv")) or to_n(d.get("now")))
        if price > 0:
            change_val = to_n(d.get("compareToPreviousClosePrice", 0))
            return {
                "price":     round(price),
                "prevClose": round(price - change_val) if change_val else round(price),
                "high52w":   round(to_n(d.get("highPrice")) or to_n(d.get("yearHighPrice"))),
                "low52w":    round(to_n(d.get("lowPrice"))  or to_n(d.get("yearLowPrice"))),
                "tradedAt":  str(d.get("localTradedAt") or d.get("dealTradeTime") or "")[:19],
                "source":    "네이버 금융",
            }
    except Exception as e:
        print(f"  네이버 basic 실패 ({code}): {e}", file=sys.stderr)
    return None


# ─────────────────────────────────────────
# 외국인·기관 순매수 (네이버 금융 HTML 파싱)
# ─────────────────────────────────────────
def fetch_investor_flow(code):
    result = {"foreign": 0, "institution": 0, "individual": 0, "foreignTrend": "중립", "comment": ""}
    try:
        # 네이버 금융 PC 버전 - 투자자별 매매동향 HTML
        raw_bytes = urlopen(Request(
            f"https://finance.naver.com/item/frgn.naver?code={code}",
            headers={"User-Agent":"Mozilla/5.0","Referer":"https://finance.naver.com/"}
        ), timeout=8).read()
        html = raw_bytes.decode("euc-kr", errors="ignore")
        import re
        # 순매수 수량 테이블 파싱 (단위: 주)
        nums = re.findall(r'<td[^>]*class="[^"]*num[^"]*"[^>]*>([^<]+)</td>', html)
        nums = [n.strip().replace(",","").replace("+","") for n in nums if n.strip()]
        nums = [int(n) for n in nums if re.match(r"^-?\d+$", n)]

        # 외국인(index 0), 기관(index 1), 개인(index 2) 순서로 나옴
        if len(nums) >= 3:
            result["foreign"]     = nums[0]
            result["institution"] = nums[1]
            result["individual"]  = nums[2]
        elif len(nums) >= 1:
            result["foreign"] = nums[0]

        # 텍스트 기반 폴백
        if result["foreign"] == 0:
            fgn_m = re.search(r'외국인[^0-9-+]*([+-]?[\d,]+)', html)
            if fgn_m:
                result["foreign"] = int(fgn_m.group(1).replace(",",""))
    except Exception as e:
        print(f"  수급 HTML 실패 ({code}): {e}", file=sys.stderr)

    # 네이버 모바일 JSON API 폴백
    if result["foreign"] == 0:
        for ep in ["investorTrade", "investor", "tradeVolume"]:
            try:
                d = http_json(f"https://m.stock.naver.com/api/stock/{code}/{ep}")
                rows = d if isinstance(d, list) else (d.get("list") or d.get("data") or [])
                for row in rows:
                    nm = str(row.get("investorType") or row.get("type") or "")
                    val = to_n(row.get("netBuySellVolume") or row.get("netBuy") or row.get("net") or 0)
                    if any(k in nm for k in ["외국","forg","FORG"]): result["foreign"] = round(val)
                    elif any(k in nm for k in ["기관","inst","INST"]): result["institution"] = round(val)
                    elif any(k in nm for k in ["개인","indiv","INDIV"]): result["individual"] = round(val)
                if result["foreign"] != 0: break
            except: pass

    f, ins = result["foreign"], result["institution"]
    if f > 0 and ins > 0:
        result["foreignTrend"] = "매수우세"
        result["comment"] = f"외국인 +{f:,}주 · 기관 +{ins:,}주 순매수. 강한 매수 압력."
    elif f > 0:
        result["foreignTrend"] = "매수우세"
        result["comment"] = f"외국인 +{f:,}주 순매수. 외국인 주도 상승 기대."
    elif f < 0 and ins < 0:
        result["foreignTrend"] = "매도우세"
        result["comment"] = f"외국인 {f:,}주 · 기관 {ins:,}주 순매도. 강한 매도 압력."
    elif f < 0:
        result["foreignTrend"] = "매도우세"
        result["comment"] = f"외국인 {f:,}주 순매도. 수급 부담 존재."
    else:
        result["foreignTrend"] = "중립"
        result["comment"] = "외국인·기관 수급 — 장중 집계 중이거나 데이터 준비 중."
    return result


# ─────────────────────────────────────────
# 공매도 비율 (네이버 금융 HTML 파싱)
# ─────────────────────────────────────────
def fetch_short_selling(code):
    try:
        html = http_get(
            f"https://finance.naver.com/item/main.naver?code={code}",
            headers={"Referer": "https://finance.naver.com/", "Accept-Language": "ko-KR"}
        )
        import re
        # 공매도 비율 파싱
        m = re.search(r"공매도[^0-9]*(\d+\.?\d*)\s*%", html)
        if m:
            ratio = float(m.group(1))
            comment = (
                "공매도 비율 높음 — 하락 압력 주의" if ratio > 5 else
                "공매도 비율 보통" if ratio > 2 else
                "공매도 비율 낮음 — 하락 압력 적음"
            )
            return {"ratio": ratio, "volume": 0, "comment": comment}
    except Exception as e:
        print(f"  공매도 HTML 실패 ({code}): {e}", file=sys.stderr)

    # JSON API 폴백
    for ep in ["shortSelling", "short"]:
        try:
            d = http_json(f"https://m.stock.naver.com/api/stock/{code}/{ep}")
            ratio = to_n(d.get("shortSellingRatio") or d.get("ratio") or 0)
            if ratio > 0:
                comment = ("공매도 비율 높음 — 하락 압력 주의" if ratio > 5 else
                           "공매도 비율 보통" if ratio > 2 else "공매도 비율 낮음")
                return {"ratio": round(ratio, 2), "volume": 0, "comment": comment}
        except: pass

    return {"ratio": 0, "volume": 0, "comment": "공매도 데이터 준비 중"}


# ─────────────────────────────────────────
# KOSPI 지수 (네이버 금융)
# ─────────────────────────────────────────
def fetch_kospi():
    try:
        d = http_json(f"https://m.stock.naver.com/api/index/KOSPI/basic")
        price = to_n(d.get("closePrice") or d.get("indexValue") or 0)
        change = to_n(d.get("compareToPreviousClosePrice") or 0)
        pct = to_n(d.get("fluctuationsRatio") or 0)
        return {"price": round(price, 2), "change": round(change, 2), "changePct": round(pct, 2)}
    except Exception as e:
        print(f"  KOSPI 실패: {e}", file=sys.stderr)
    return {"price": 0, "change": 0, "changePct": 0}


# ─────────────────────────────────────────
# 증권사 목표주가 + 적정주가 (네이버 금융)
# ─────────────────────────────────────────
def fetch_target_price(code):
    """증권사 목표주가 - 네이버 컨센서스 → 실패 시 하드코딩 폴백
    (FnGuide는 해외서버 406 차단으로 제외. 실전 API 연결 시 KIS로 대체 가능)"""
    result = {"consensus": 0, "high": 0, "low": 0, "avg": 0,
              "count": 0, "comment": "", "source": ""}
    try:
        # 네이버 모바일 API
        for ep in [
            f"https://m.stock.naver.com/api/stock/{code}/consensus",
            f"https://m.stock.naver.com/api/stock/{code}/analysisSummary",
        ]:
            try:
                d = http_json(ep)
                tp_vals = []
                items = d if isinstance(d, list) else [d]
                for x in items:
                    v = to_n(x.get("targetPrice") or x.get("priceTarget") or
                             x.get("tp") or x.get("consensusPrice") or 0)
                    if v >= 50000:
                        tp_vals.append(round(v))
                if tp_vals:
                    result["consensus"] = round(sum(tp_vals)/len(tp_vals))
                    result["high"]      = max(tp_vals)
                    result["low"]       = min(tp_vals)
                    result["avg"]       = result["consensus"]
                    result["count"]     = len(tp_vals)
                    result["source"]    = "네이버 컨센서스"
                    return result
            except: pass
    except: pass

    # 최종 폴백: 증권사 컨센서스 하드코딩 (2026년 5월 기준)
    fallback = {
        "000660": {"consensus": 2800000, "high": 3000000, "low": 2300000, "count": 15},
        "005930": {"consensus": 420000,  "high": 500000,  "low": 350000,  "count": 20},
        "066570": {"consensus": 280000,  "high": 320000,  "low": 240000,  "count": 12},
        "009150": {"consensus": 180000,  "high": 210000,  "low": 150000,  "count": 10},
        "005380": {"consensus": 280000,  "high": 320000,  "low": 240000,  "count": 12},
    }
    if code in fallback:
        f = fallback[code]
        result.update({**f, "source": "증권사 컨센서스 (2026.05)"})
    return result

def calc_fair_value(code, price, eps=None, bps=None, growth=None):
    """PER·PBR 기반 적정주가 계산"""
    # 종목별 업종 평균 PER/PBR (2026 기준)
    sector_data = {
        "000660": {"per": 12, "pbr": 1.8, "name": "반도체"},    # SK하이닉스
        "005930": {"per": 14, "pbr": 1.5, "name": "반도체"},    # 삼성전자
        "066570": {"per": 10, "pbr": 0.9, "name": "가전/전장"}, # LG전자
        "009150": {"per": 15, "pbr": 1.6, "name": "전자부품"},  # 삼성전기
        "005380": {"per": 8,  "pbr": 0.7, "name": "자동차"},    # 현대자동차
    }
    sd = sector_data.get(code, {"per": 12, "pbr": 1.5, "name": "일반"})

    results = {}

    # EPS 기반 PER 적정주가 (Yahoo Finance에서 EPS 사용)
    if eps and eps > 0:
        results["per_fair"] = round(eps * sd["per"])
        results["per_label"] = f"업종 평균 PER {sd['per']}배"

    # BPS 기반 PBR 적정주가
    if bps and bps > 0:
        results["pbr_fair"] = round(bps * sd["pbr"])
        results["pbr_label"] = f"업종 평균 PBR {sd['pbr']}배"

    # 종합 적정주가 (PER·PBR 평균)
    vals = [v for k, v in results.items() if k.endswith("_fair")]
    if vals:
        results["fair_value"] = round(sum(vals) / len(vals))
    else:
        results["fair_value"] = 0

    # 현재가 대비 괴리율
    if results["fair_value"] > 0 and price > 0:
        gap = round((results["fair_value"] - price) / price * 100, 1)
        results["gap"] = gap
        results["gap_comment"] = (
            f"적정가 대비 {abs(gap)}% {'저평가 — 매수 기회' if gap > 5 else '고평가 — 주의' if gap < -5 else '적정 수준'}"
        )
    else:
        results["gap"] = 0
        results["gap_comment"] = "EPS/BPS 데이터 부족"

    results["sector"] = sd["name"]
    results["sector_per"] = sd["per"]
    results["sector_pbr"] = sd["pbr"]
    return results


def fetch_financial_data(yf_sym, code):
    """Yahoo Finance에서 EPS, BPS 수집"""
    eps = bps = None
    try:
        d = http_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}?interval=1d&range=1d")
        meta = d.get("chart", {}).get("result", [{}])[0].get("meta", {})
        # EPS
        eps_val = meta.get("epsTrailingTwelveMonths") or meta.get("eps")
        if eps_val:
            # Yahoo는 달러 기준이므로 환율 적용 (약 1350원)
            eps = round(float(eps_val) * 1350) if float(eps_val) < 1000 else round(float(eps_val))
        # BPS (book value per share)
        bps_val = meta.get("bookValue")
        if bps_val:
            bps = round(float(bps_val) * 1350) if float(bps_val) < 1000 else round(float(bps_val))
    except Exception as e:
        print(f"  재무데이터 실패 ({yf_sym}): {e}", file=sys.stderr)

    # EPS 없으면 종목별 추정값 사용 (2025 실적 기준)
    fallback = {
        "000660": {"eps": 180000, "bps": 950000},  # SK하이닉스
        "005930": {"eps": 20000,  "bps": 180000},  # 삼성전자
        "066570": {"eps": 18000,  "bps": 195000},  # LG전자
        "009150": {"eps": 12000,  "bps": 120000},  # 삼성전기
        "005380": {"eps": 45000,  "bps": 380000},  # 현대자동차
    }
    if not eps and code in fallback:
        eps = fallback[code]["eps"]
        bps = fallback[code]["bps"]
    return eps, bps


# ─────────────────────────────────────────
# Yahoo Finance OHLCV (일봉 + 주봉)
# ─────────────────────────────────────────
def fetch_yahoo_ohlcv(yf_sym, interval="1d", range_="60d"):
    for base in ["query1", "query2"]:
        try:
            url = (f"https://{base}.finance.yahoo.com/v8/finance/chart/{yf_sym}"
                   f"?interval={interval}&range={range_}&includePrePost=false")
            d = http_json(url)
            result = d.get("chart", {}).get("result", [None])[0]
            if not result:
                continue
            meta = result.get("meta", {})
            ts = result.get("timestamp", []) or []
            q = result.get("indicators", {}).get("quote", [{}])[0]
            candles = []
            for i, t in enumerate(ts):
                close = (q.get("close") or [])[i] if i < len(q.get("close") or []) else None
                if close is None:
                    continue
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


# ─────────────────────────────────────────
# ─────────────────────────────────────────
# ─────────────────────────────────────────
# 뉴스 수집 (연합뉴스·한경·머니투데이 RSS)
# ─────────────────────────────────────────
def fetch_news(code, name, limit=5):
    news_list = []
    pos_kw = ["급등","상승","호실적","매수","신고가","수주","흑자","개선","증가","성장","강세","돌파","반등","최고","어닝","깜짝"]
    neg_kw = ["급락","하락","부진","매도","신저가","적자","감소","둔화","약세","리스크","우려","경고","폭락","손실","실망","쇼크"]

    import xml.etree.ElementTree as ET
    from email.utils import parsedate

    from urllib.parse import quote
    # RSS 소스 (해외서버에서 접근 가능한 것만)
    rss_sources = [
        # Yahoo Finance (해외서버 안정적)
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={code}.KS&region=KR&lang=ko-KR",
    ]

    seen = set()
    for rss_url in rss_sources:
        if len(news_list) >= limit:
            break
        try:
            xml_str = http_get(rss_url, timeout=12)
            root = ET.fromstring(xml_str)
            items = root.findall(".//item")
            for item in items:
                title = item.findtext("title") or ""
                pub   = item.findtext("pubDate") or ""
                link  = item.findtext("link") or ""

                # 종목 관련 뉴스만 필터링
                if name not in title and code not in title:
                    # 반도체/가전/전자 관련 키워드도 허용
                    sector_kw = {
                        "000660": ["SK하이닉스","하이닉스","HBM","반도체","메모리"],
                        "005930": ["삼성전자","삼성","갤럭시","파운드리","반도체"],
                        "066570": ["LG전자","LG","가전","전장","OLED"],
                        "009150": ["삼성전기","MLCC","패키지기판","전자부품"],
                        "005380": ["현대자동차","현대차","아이오닉","제네시스","전기차"],
                    }
                    kws = sector_kw.get(code, [name])
                    if not any(k in title for k in kws):
                        continue

                # 날짜 파싱
                try:
                    pd = parsedate(pub)
                    date_str = f"{pd[0]}-{pd[1]:02d}-{pd[2]:02d}" if pd else pub[:10]
                except:
                    date_str = pub[:10] if pub else ""

                # 출처 제거
                clean = title.split(" - ")[0].split(" | ")[0].strip()
                if not clean or clean in seen:
                    continue
                seen.add(clean)

                sentiment = "긍정" if any(k in clean for k in pos_kw) else                             "부정" if any(k in clean for k in neg_kw) else "중립"
                news_list.append({
                    "title": clean[:60],
                    "date": date_str,
                    "sentiment": sentiment,
                    "url": link,
                })
                if len(news_list) >= limit:
                    break
        except Exception:
            pass  # RSS 차단/실패는 조용히 무시 (뉴스는 보조 정보)

    return news_list

# ─────────────────────────────────────────
# DART 공시 (공식 OpenDART API)
# ─────────────────────────────────────────
# DART corp_code 매핑 (종목코드 → DART 고유번호)
DART_CORP_CODE = {
    "000660": "00164779",  # SK하이닉스
    "005930": "00126380",  # 삼성전자
    "066570": "00401731",  # LG전자
    "009150": "00164488",  # 삼성전기
    "005380": "00164742",  # 현대자동차
}

def fetch_dart(code, limit=5):
    dart_list = []
    if not DART_API_KEY:
        print("  DART API 키 없음", file=sys.stderr)
        return dart_list
    try:
        from datetime import datetime, timedelta
        end = datetime.now(KST).strftime("%Y%m%d")
        start = (datetime.now(KST) - timedelta(days=90)).strftime("%Y%m%d")
        # corp_code 사용 (stock_code보다 안정적)
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
        items = d.get("list") or []
        important_kw = ["실적","분기","연간","배당","유상증자","무상증자","합병","분할","자사주","대규모","공개매수","주요사항"]
        for item in items[:limit]:
            title = item.get("report_nm") or ""
            date  = item.get("rcept_dt") or ""
            rcept = item.get("rcept_no") or ""
            corp  = item.get("corp_name") or ""
            if title:
                is_important = any(k in title for k in important_kw)
                dart_list.append({
                    "title": title[:50],
                    "date": str(date)[:10],
                    "important": is_important,
                    "corp": corp,
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}" if rcept else "",
                })
        print(f"  ✅ DART {code}: {len(dart_list)}건", file=sys.stderr)
    except Exception as e:
        print(f"  DART 실패 ({code}): {e}", file=sys.stderr)
    return dart_list


# ─────────────────────────────────────────
# 기술 지표
# ─────────────────────────────────────────
def ema(arr, p):
    if len(arr) < p:
        return [None] * len(arr)
    k = 2 / (p + 1)
    e = sum(arr[:p]) / p
    res = [None] * (p - 1) + [e]
    for i in range(p, len(arr)):
        e = arr[i] * k + e * (1 - k)
        res.append(e)
    return res


def calc_rsi(closes, p=14):
    if len(closes) < p + 1:
        return None
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
    if len(closes) < 35:
        return None, None, None
    e12, e26 = ema(closes, 12), ema(closes, 26)
    ml = [a - b for a, b in zip(e12, e26) if a is not None and b is not None]
    if len(ml) < 9:
        return None, None, None
    sig = ema(ml, 9)
    # 주가 대비 % 정규화 (단위 통일)
    base = closes[-1] if closes[-1] != 0 else 1
    m = round(ml[-1] / base * 100, 3)
    s = round(sig[-1] / base * 100, 3)
    return m, s, round(m - s, 3)


def calc_stoch(highs, lows, closes, p=14):
    if len(closes) < p:
        return None
    hh, ll = max(highs[-p:]), min(lows[-p:])
    return 50.0 if hh == ll else round((closes[-1] - ll) / (hh - ll) * 100, 1)


def calc_stoch_rsi(closes, rsi_period=14, stoch_period=14, k=3, d=3):
    """스토캐스틱 RSI - RSI에 스토캐스틱을 적용한 정밀 모멘텀 지표
    일반 RSI보다 과매수·과매도 신호가 더 민감하고 빠르게 잡힘
    반환: {"k": %K값, "d": %D값, "signal": 매수/매도/중립, "comment": 해석}"""
    # 데이터 최소 35개만 있으면 시도 (60일이 안 와도 작동하도록)
    if len(closes) < 35:
        print(f"  StochRSI: 데이터 {len(closes)}개 부족 (35개 필요)", file=sys.stderr)
        return None
    # RSI 시계열 생성 - 가능한 최대한
    rsi_series = []
    for i in range(rsi_period, len(closes)):
        sub = closes[:i + 1]
        r = calc_rsi(sub, rsi_period)
        if r is not None:
            rsi_series.append(r)
    if len(rsi_series) < stoch_period + d:
        print(f"  StochRSI: RSI 시계열 {len(rsi_series)}개 부족", file=sys.stderr)
        return None
    # raw %K 시계열
    raw_k = []
    for i in range(stoch_period - 1, len(rsi_series)):
        window = rsi_series[i - stoch_period + 1: i + 1]
        hh, ll = max(window), min(window)
        kv = 50.0 if hh == ll else (rsi_series[i] - ll) / (hh - ll) * 100
        raw_k.append(kv)
    if len(raw_k) < d * 2:
        print(f"  StochRSI: raw_k {len(raw_k)}개 부족 ({d*2} 필요)", file=sys.stderr)
        return None
    # %K = raw K의 d평활, %D = %K의 d평활
    k_smoothed = []
    for i in range(d - 1, len(raw_k)):
        k_smoothed.append(sum(raw_k[i - d + 1: i + 1]) / d)
    if len(k_smoothed) < d:
        return None
    k_val = k_smoothed[-1]
    d_val = sum(k_smoothed[-d:]) / d

    if k_val < 20 and d_val < 20:
        signal, comment = "매수", f"과매도 구간({k_val:.0f}) — 반등 임박 신호"
    elif k_val > 80 and d_val > 80:
        signal, comment = "매도", f"과매수 구간({k_val:.0f}) — 단기 조정 주의"
    elif k_val > d_val and k_val < 30:
        signal, comment = "매수", "저점 골든크로스 — 매수 우위"
    elif k_val < d_val and k_val > 70:
        signal, comment = "매도", "고점 데드크로스 — 매도 우위"
    else:
        signal, comment = "중립", f"K {k_val:.0f} / D {d_val:.0f}"
    return {"k": round(k_val, 1), "d": round(d_val, 1),
            "signal": signal, "comment": comment}


def detect_divergence(closes, highs, lows, lookback=20):
    """다이버전스 감지 - 주가와 RSI의 방향 괴리를 잡아냄
    강세 다이버전스: 주가는 신저점, RSI는 더 높음 → 바닥 반등 신호 (매수)
    약세 다이버전스: 주가는 신고점, RSI는 더 낮음 → 천장 하락 신호 (매도)"""
    if len(closes) < lookback + 14:
        return None
    # 최근 lookback 구간에서 두 개의 저점/고점 찾기
    recent_low_idx = lows[-lookback:].index(min(lows[-lookback:])) + (len(lows) - lookback)
    recent_high_idx = highs[-lookback:].index(max(highs[-lookback:])) + (len(highs) - lookback)
    # 이전 구간의 저점/고점 (lookback 더 이전)
    if recent_low_idx < 14 or recent_high_idx < 14:
        return None
    prev_window_lows = lows[max(0, recent_low_idx - lookback):recent_low_idx]
    prev_window_highs = highs[max(0, recent_high_idx - lookback):recent_high_idx]
    if not prev_window_lows or not prev_window_highs:
        return None
    prev_low_idx = prev_window_lows.index(min(prev_window_lows)) + max(0, recent_low_idx - lookback)
    prev_high_idx = prev_window_highs.index(max(prev_window_highs)) + max(0, recent_high_idx - lookback)

    # 각 시점의 RSI 계산
    def rsi_at(idx):
        if idx < 14: return None
        return calc_rsi(closes[:idx + 1], 14)

    rsi_recent_low = rsi_at(recent_low_idx)
    rsi_prev_low   = rsi_at(prev_low_idx)
    rsi_recent_high = rsi_at(recent_high_idx)
    rsi_prev_high   = rsi_at(prev_high_idx)

    bullish = False
    bearish = False
    comment = ""

    # 강세 다이버전스: 주가는 더 낮은 저점, RSI는 더 높은 저점
    if (rsi_recent_low is not None and rsi_prev_low is not None
            and lows[recent_low_idx] < lows[prev_low_idx]
            and rsi_recent_low > rsi_prev_low
            and rsi_recent_low < 40):
        bullish = True
        comment = f"강세 다이버전스 — 주가 ↓ / RSI ↑ ({rsi_prev_low:.0f}→{rsi_recent_low:.0f}). 바닥권 반등 신호"

    # 약세 다이버전스: 주가는 더 높은 고점, RSI는 더 낮은 고점
    if (rsi_recent_high is not None and rsi_prev_high is not None
            and highs[recent_high_idx] > highs[prev_high_idx]
            and rsi_recent_high < rsi_prev_high
            and rsi_recent_high > 60):
        bearish = True
        if comment:
            comment += " | "
        comment += f"약세 다이버전스 — 주가 ↑ / RSI ↓ ({rsi_prev_high:.0f}→{rsi_recent_high:.0f}). 천장권 하락 신호"

    if not bullish and not bearish:
        return None
    return {
        "bullish": bullish,
        "bearish": bearish,
        "signal": "매수" if bullish and not bearish else "매도" if bearish and not bullish else "혼조",
        "comment": comment,
    }


def calc_ichimoku(highs, lows, closes):
    """일목균형표 - 한국 차트에서 강력한 추세·지지·저항 지표
    전환선·기준선 교차, 구름대(선행스팬1·2) 돌파가 핵심 신호
    반환: tenkan, kijun, senkou_a, senkou_b, signal, comment"""
    if len(closes) < 52:
        return None

    def mid(h_window, l_window):
        return (max(h_window) + min(l_window)) / 2

    # 전환선 (9일): (9일 최고 + 9일 최저) / 2
    tenkan = mid(highs[-9:], lows[-9:])
    # 기준선 (26일): (26일 최고 + 26일 최저) / 2
    kijun = mid(highs[-26:], lows[-26:])
    # 선행스팬1 (전환선+기준선)/2, 26일 앞으로 그림
    senkou_a = (tenkan + kijun) / 2
    # 선행스팬2 (52일 최고+최저)/2
    senkou_b = mid(highs[-52:], lows[-52:])

    price = closes[-1]
    cloud_top = max(senkou_a, senkou_b)
    cloud_bot = min(senkou_a, senkou_b)

    # 신호 판정
    if price > cloud_top and tenkan > kijun:
        signal = "매수"
        comment = "구름대 위 + 전환선>기준선 — 강한 상승 추세"
    elif price > cloud_top:
        signal = "매수"
        comment = "구름대 위 — 상승 우위"
    elif price < cloud_bot and tenkan < kijun:
        signal = "매도"
        comment = "구름대 아래 + 전환선<기준선 — 강한 하락 추세"
    elif price < cloud_bot:
        signal = "매도"
        comment = "구름대 아래 — 하락 우위"
    else:
        signal = "중립"
        comment = "구름대 내부 — 방향성 미정 (변곡점 임박)"

    return {
        "tenkan": round(tenkan),
        "kijun": round(kijun),
        "senkouA": round(senkou_a),
        "senkouB": round(senkou_b),
        "cloudTop": round(cloud_top),
        "cloudBot": round(cloud_bot),
        "signal": signal,
        "comment": comment,
    }


def calc_cci(highs, lows, closes, p=20):
    """CCI (Commodity Channel Index) - 가격이 이동평균에서 얼마나 이탈했는지 측정
    +100 이상 과매수, -100 이하 과매도. 추세 이탈을 빠르게 잡음
    반환: value, signal, comment"""
    if len(closes) < p:
        return None
    typical = [(h + l + c) / 3 for h, l, c in zip(highs[-p:], lows[-p:], closes[-p:])]
    sma_tp = sum(typical) / p
    mad = sum(abs(t - sma_tp) for t in typical) / p
    if mad == 0:
        return {"value": 0, "signal": "중립", "comment": "변동성 없음"}
    cci = (typical[-1] - sma_tp) / (0.015 * mad)

    if cci > 200:
        signal, comment = "매도", f"극단 과매수({cci:.0f}) — 강한 조정 신호"
    elif cci > 100:
        signal, comment = "매도", f"과매수({cci:.0f}) — 조정 주의"
    elif cci < -200:
        signal, comment = "매수", f"극단 과매도({cci:.0f}) — 강한 반등 신호"
    elif cci < -100:
        signal, comment = "매수", f"과매도({cci:.0f}) — 반등 임박"
    else:
        signal, comment = "중립", f"중립 구간({cci:.0f})"
    return {"value": round(cci, 1), "signal": signal, "comment": comment}


def calc_psar(highs, lows, closes, af_start=0.02, af_step=0.02, af_max=0.20):
    """파라볼릭 SAR (Stop And Reverse) - 추세 전환점 표시
    점이 가격 아래면 상승 추세, 위면 하락 추세. 손절선으로 활용
    반환: psar(현재 SAR값), trend(상승/하락), signal, comment"""
    if len(closes) < 10:
        return None
    # 초기값
    trend_up = closes[1] > closes[0]
    psar = lows[0] if trend_up else highs[0]
    ep = highs[1] if trend_up else lows[1]  # 극값(Extreme Point)
    af = af_start

    for i in range(2, len(closes)):
        prev_psar = psar
        # SAR 업데이트
        psar = prev_psar + af * (ep - prev_psar)

        if trend_up:
            psar = min(psar, lows[i - 1], lows[i - 2] if i >= 2 else lows[i - 1])
            if lows[i] < psar:
                # 추세 전환 (상승 → 하락)
                trend_up = False
                psar = ep
                ep = lows[i]
                af = af_start
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(af + af_step, af_max)
        else:
            psar = max(psar, highs[i - 1], highs[i - 2] if i >= 2 else highs[i - 1])
            if highs[i] > psar:
                # 추세 전환 (하락 → 상승)
                trend_up = True
                psar = ep
                ep = highs[i]
                af = af_start
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(af + af_step, af_max)

    # 최근 5일 안에 전환됐는지 확인 (신호 강도)
    recent_flip = False
    if len(closes) >= 6:
        # 마지막 5봉 동안 trend가 바뀌었는지는 별도 계산이 필요하지만
        # 단순화: 현재 psar과 가격이 가까우면 전환 임박
        gap_pct = abs(closes[-1] - psar) / closes[-1] * 100
        recent_flip = gap_pct < 1.5

    trend = "상승" if trend_up else "하락"
    if trend_up:
        signal = "매수"
        comment = f"상승 추세 유지 (손절선 ₩{round(psar):,})"
        if recent_flip:
            comment += " — 전환 주의"
    else:
        signal = "매도"
        comment = f"하락 추세 (저항선 ₩{round(psar):,})"
        if recent_flip:
            comment += " — 상승 전환 가능성"

    return {
        "psar": round(psar),
        "trend": trend,
        "signal": signal,
        "comment": comment,
        "nearFlip": recent_flip,
    }


def calc_value_surge(closes, volumes, p=20):
    """거래대금 급증 - 가격×거래량으로 실제 자금 유입 측정
    단순 거래량 급증보다 세력 진입 포착이 정확함
    반환: ratio, surge(여부), comment"""
    if len(closes) < p + 1:
        return None
    # 거래대금 = 가격 × 거래량
    values = [c * v for c, v in zip(closes, volumes)]
    recent = values[-1]
    avg = sum(values[-p - 1:-1]) / p
    if avg == 0:
        return {"ratio": 1.0, "surge": False, "comment": "데이터 부족"}
    ratio = recent / avg

    if ratio > 3.0:
        comment = f"거래대금 급증 ({ratio:.1f}배) — 강한 세력 진입"
        surge = True
    elif ratio > 2.0:
        comment = f"거래대금 증가 ({ratio:.1f}배) — 자금 유입 가시화"
        surge = True
    elif ratio > 1.5:
        comment = f"거래대금 소폭 증가 ({ratio:.1f}배)"
        surge = False
    elif ratio < 0.5:
        comment = f"거래대금 감소 ({ratio:.1f}배) — 관심 이탈"
        surge = False
    else:
        comment = f"거래대금 평이 ({ratio:.1f}배)"
        surge = False
    return {"ratio": round(ratio, 2), "surge": surge, "comment": comment}


def calc_sma(arr, p):
    if len(arr) < p:
        return None
    return round(sum(arr[-p:]) / p)


def calc_williams(highs, lows, closes, p=14):
    if len(closes) < p:
        return None
    hh, ll = max(highs[-p:]), min(lows[-p:])
    return -50.0 if hh == ll else round((hh - closes[-1]) / (hh - ll) * -100, 1)


def calc_adx(highs, lows, closes, p=14):
    if len(closes) < p + 1:
        return None
    tr, pdm, ndm = [], [], []
    for i in range(1, len(closes)):
        h, l, ph, pl, pc = highs[i], lows[i], highs[i-1], lows[i-1], closes[i-1]
        tr.append(max(h-l, abs(h-pc), abs(l-pc)))
        pdm.append(max(h-ph, 0) if max(h-ph, 0) > (pl-l) else 0)
        ndm.append(max(pl-l, 0) if max(pl-l, 0) > (h-ph) else 0)
    atr = sum(tr[-p:]) / p
    if atr == 0:
        return None
    pdi = sum(pdm[-p:]) / p / atr * 100
    ndi = sum(ndm[-p:]) / p / atr * 100
    dx = 0 if (pdi + ndi) == 0 else abs(pdi - ndi) / (pdi + ndi) * 100
    return {
        "adx": round(dx, 1), "pdi": round(pdi, 1), "ndi": round(ndi, 1),
        "strength": "강한 추세" if dx > 25 else "약한 추세" if dx > 20 else "추세 없음(횡보)"
    }


def calc_mfi(highs, lows, closes, volumes, p=14):
    if len(closes) < p + 1:
        return None
    pmf = nmf = 0
    for i in range(len(closes) - p, len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        ptp = (highs[i-1] + lows[i-1] + closes[i-1]) / 3
        mf = tp * volumes[i]
        if tp > ptp: pmf += mf
        else: nmf += mf
    return 100.0 if nmf == 0 else round(100 - 100 / (1 + pmf / nmf), 1)


def calc_vwap(highs, lows, closes, volumes, p=5):
    if len(closes) < p:
        return None
    tv = sv = 0
    for i in range(-p, 0):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        tv += tp * volumes[i]; sv += volumes[i]
    return None if sv == 0 else round(tv / sv)


def calc_boll(closes, p=20):
    if len(closes) < p:
        return None
    sl = closes[-p:]
    m = sum(sl) / p
    std = (sum((v - m) ** 2 for v in sl) / p) ** 0.5
    return {"upper": m + 2 * std, "mid": m, "lower": m - 2 * std}

def calc_pivot(highs, lows, closes):
    if len(closes) < 2:
        return None
    h, l, c = highs[-2], lows[-2], closes[-2]
    p = (h + l + c) / 3
    return {
        "p": round(p), "r1": round(2*p-l), "r2": round(p+(h-l)),
        "s1": round(2*p-h), "s2": round(p-(h-l))
    }


def calc_obv(closes, volumes):
    if len(closes) < 2:
        return {"obv": 0, "slope": 0, "trend": "횡보"}
    obv = 0; arr = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]: obv += volumes[i]
        elif closes[i] < closes[i-1]: obv -= volumes[i]
        arr.append(obv)
    recent = arr[-5:]
    base = recent[0] if recent[0] != 0 else 1
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
    # RSI: 가장 중요 지표 (가중치 높음)
    if rsi is not None: score += (rsi - 50) * 0.5
    # 스토캐스틱
    if stoch is not None: score += (stoch - 50) * 0.15
    # OBV 기울기: 극단값 클리핑 (-10~+10 범위로 제한)
    if obv_slope is not None:
        clipped = max(min(obv_slope, 10), -10)
        score += clipped * 0.3
    # ADX: 추세 강도
    if adx_val is not None: score += 3 if adx_val > 30 else (-3 if adx_val < 15 else 0)
    # 거래량 비율
    if vol_ratio is not None: score += 4 if vol_ratio > 1.5 else (-4 if vol_ratio < 0.6 else 0)
    score = min(max(round(score), 0), 100)
    label = ("극단적 탐욕" if score >= 75 else "탐욕" if score >= 60
             else "중립" if score >= 40 else "공포" if score >= 25 else "극단적 공포")
    color = ("#ff4d6d" if score >= 75 else "#ffd166" if score >= 60
             else "#9ab" if score >= 40 else "#ffd166" if score >= 25 else "#00e676")
    return {"score": score, "label": label, "color": color}


# ─────────────────────────────────────────
# 멀티 타임프레임 신호
# ─────────────────────────────────────────
def calc_weekly_signal(weekly_candles):
    if len(weekly_candles) < 10:
        return {"opinion": "중립", "rsi": None, "macd": None, "comment": "데이터 부족"}
    closes = [c["close"] for c in weekly_candles]
    rsi = calc_rsi(closes)
    macd, sig, hist = calc_macd(closes)
    opinion = "중립"
    if rsi and macd and sig:
        if rsi < 45 and macd > sig: opinion = "매수"
        elif rsi > 60 and macd < sig: opinion = "매도"
    comment = f"주봉 RSI {rsi or '-'} · MACD {'골든크로스' if macd and sig and macd > sig else '데드크로스' if macd and sig else '-'}"
    return {"opinion": opinion, "rsi": rsi, "macd": macd, "macdSignal": sig, "comment": comment}


# ─────────────────────────────────────────
# KOSPI 상대강도
# ─────────────────────────────────────────
def calc_relative_strength(stock_pct, kospi_pct):
    if kospi_pct == 0:
        return {"rs": 0, "comment": "KOSPI 데이터 없음", "strong": False}
    rs = round(stock_pct - kospi_pct, 2)
    strong = rs > 0
    comment = (f"KOSPI 대비 +{rs}%p 강세 — 시장 아웃퍼폼" if rs > 1 else
               f"KOSPI 대비 {rs}%p 약세 — 시장 언더퍼폼" if rs < -1 else
               "KOSPI 대비 중립")
    return {"rs": rs, "comment": comment, "strong": strong}


# ─────────────────────────────────────────
# 52주 신고가 근접 감지
# ─────────────────────────────────────────
def check_52w_breakout(price, high52w, low52w):
    if high52w == 0:
        return {"nearHigh": False, "nearLow": False, "position": 50, "comment": ""}
    pos = round((price - low52w) / (high52w - low52w) * 100) if high52w != low52w else 50
    near_high = pos >= 90
    near_low  = pos <= 10
    comment = (f"52주 신고가 근접 ({pos}%) — 돌파 시 강한 매수 신호" if near_high else
               f"52주 신저가 근접 ({pos}%) — 반등 매수 기회" if near_low else
               f"52주 고저 중간 위치 ({pos}%)")
    return {"nearHigh": near_high, "nearLow": near_low, "position": pos, "comment": comment}


# ─────────────────────────────────────────
# 거래량 급증 감지
# ─────────────────────────────────────────
def check_volume_surge(volumes):
    if len(volumes) < 10:
        return {"surge": False, "ratio": 1.0, "comment": "데이터 부족"}
    avg = sum(volumes[-20:]) / min(20, len(volumes))
    latest = volumes[-1]
    ratio = round(latest / avg, 2) if avg > 0 else 1.0
    surge = ratio >= 2.0
    comment = (f"거래량 급증 ({ratio}x) — 세력 진입 가능성" if ratio >= 2.0 else
               f"거래량 증가 ({ratio}x)" if ratio >= 1.3 else
               f"거래량 평이 ({ratio}x)")
    return {"surge": surge, "ratio": ratio, "comment": comment}


# ─────────────────────────────────────────
# 역발상 신호
# ─────────────────────────────────────────
def contra_signal(rsi, macd, macd_sig, obv, patterns, fg_score, investor):
    signals, strength = [], 0
    if rsi and rsi > 65 and obv and obv["slope"] < -1:
        signals.append("🔴 RSI 고점 + OBV 하락 → 가격 하락 선행 신호")
        strength -= 2
    if rsi and rsi < 35 and obv and obv["slope"] > 1:
        signals.append("🟢 RSI 저점 + OBV 상승 → 세력 매집 가능성")
        strength += 2
    if fg_score < 25:
        signals.append("🟢 극단적 공포 → 역발상 매수 기회")
        strength += 2
    if fg_score > 75:
        signals.append("🔴 극단적 탐욕 → 역발상 매도 기회")
        strength -= 2
    if macd and macd_sig:
        if macd > macd_sig and rsi and rsi > 70:
            signals.append("⚠️ MACD 상승 + RSI 과매수 → AI 매수신호 과잉 주의")
        if macd < macd_sig and rsi and rsi < 30:
            signals.append("⚠️ MACD 하락 + RSI 과매도 → AI 매도신호 과잉 주의")
    # 외국인·기관 역발상
    if investor:
        f, inst = investor.get("foreign", 0), investor.get("institution", 0)
        if f < 0 and inst < 0 and rsi and rsi < 35:
            signals.append("🟢 외국인·기관 매도 + RSI 과매도 → 패닉셀 역매수 기회")
            strength += 1
        if f > 0 and inst > 0 and rsi and rsi > 70:
            signals.append("⚠️ 외국인·기관 매수 + RSI 과매수 → 고점 매수 주의")
            strength -= 1
    bull = len([p for p in patterns if "상승" in p["type"]])
    bear = len([p for p in patterns if "하락" in p["type"]])
    if bull >= 2: signals.append("⚠️ 상승 패턴 다수 → 차익실현 주의"); strength -= 1
    if bear >= 2: signals.append("⚠️ 하락 패턴 다수 → 역매수 기회 탐색"); strength += 1
    action = ("역발상 매수 기회" if strength >= 2 else
              "역발상 매도 기회" if strength <= -2 else "현 신호 유효")
    return {"signals": signals[:4], "action": action, "strength": strength}


# ─────────────────────────────────────────
# 마스터 신호 (고도화)
# ─────────────────────────────────────────
def master_signal(rsi, macd, macd_sig, stoch, wr, mfi, adx, obv,
                  closes, price, h52, l52, vwap,
                  weekly_opinion, investor, short_ratio, news_list,
                  stoch_rsi=None, divergence=None,
                  ichimoku=None, cci=None, psar=None, value_surge=None):
    score = 0

    # 기술 지표 (일봉)
    if rsi: score += 2 if rsi < 30 else 1 if rsi < 45 else -2 if rsi > 70 else -1 if rsi > 60 else 0
    if macd and macd_sig: score += 2 if macd > macd_sig else -2 if macd < macd_sig else 0
    if wr: score += 1 if wr < -80 else -1 if wr > -20 else 0
    if stoch: score += 1 if stoch < 20 else -1 if stoch > 80 else 0
    if mfi: score += 1 if mfi < 20 else -1 if mfi > 80 else 0
    if obv: score += 1 if obv["slope"] > 2 else -1 if obv["slope"] < -2 else 0
    if adx and adx["adx"] < 15: score = round(score * 0.7)

    # 스토캐스틱 RSI (정밀 모멘텀)
    if stoch_rsi:
        if stoch_rsi["signal"] == "매수": score += 2
        elif stoch_rsi["signal"] == "매도": score -= 2

    # 다이버전스 (강한 반전 신호)
    if divergence:
        if divergence.get("bullish"): score += 3
        if divergence.get("bearish"): score -= 3

    # 일목균형표 (추세·구름대)
    if ichimoku:
        if ichimoku["signal"] == "매수":
            # 강한 상승(구름대 위+전환선>기준선)이면 +2, 약한 상승은 +1
            score += 2 if "강한" in ichimoku["comment"] else 1
        elif ichimoku["signal"] == "매도":
            score -= 2 if "강한" in ichimoku["comment"] else 1

    # CCI (추세 이탈)
    if cci:
        if cci["signal"] == "매수":
            score += 2 if "극단" in cci["comment"] else 1
        elif cci["signal"] == "매도":
            score -= 2 if "극단" in cci["comment"] else 1

    # 파라볼릭 SAR (추세 방향)
    if psar:
        if psar["signal"] == "매수": score += 1
        elif psar["signal"] == "매도": score -= 1

    # 거래대금 급증 (세력 진입)
    if value_surge and value_surge["surge"]:
        # 상승 추세에서 거래대금 급증이면 매수 가중, 하락 추세면 매도 가중
        if rsi and rsi > 50:
            score += 1
        elif rsi and rsi < 50:
            score -= 1

    # 52주 위치
    if h52 > 0 and l52 > 0:
        pos = (price - l52) / (h52 - l52) * 100
        score += 1 if pos < 20 else -1 if pos > 85 else 0

    # VWAP
    if vwap: score += 1 if price > vwap else -1

    # 이평
    s5, s20 = calc_sma(closes, 5), calc_sma(closes, 20)
    if s5 and s20: score += 1 if s5 > s20 else -1

    # 주봉 신호 (멀티 타임프레임) — 가중치 2
    if weekly_opinion == "매수": score += 2
    elif weekly_opinion == "매도": score -= 2

    # 외국인·기관 수급 — 데이터가 실제로 있을 때만 점수에 반영
    # (모의투자 API 한계로 수급 데이터가 0인 경우 점수에 영향 주지 않음)
    if investor:
        f, inst = investor.get("foreign", 0), investor.get("institution", 0)
        if f != 0 or inst != 0:  # 실제 데이터가 있을 때만
            if f > 0 and inst > 0: score += 2
            elif f > 0 or inst > 0: score += 1
            elif f < 0 and inst < 0: score -= 2
            elif f < 0 or inst < 0: score -= 1

    # 공매도 — 데이터가 실제로 있을 때만 점수에 반영
    # (0%는 "공매도 매우 낮음"이 아니라 "데이터 없음" 의미)
    if short_ratio > 0:
        if short_ratio > 5: score -= 1
        elif short_ratio < 1: score += 1

    # 뉴스 감성
    if news_list:
        pos_count = len([n for n in news_list if n["sentiment"] == "긍정"])
        neg_count = len([n for n in news_list if n["sentiment"] == "부정"])
        if pos_count > neg_count: score += 1
        elif neg_count > pos_count: score -= 1

    # 임계값: 1+2단계 지표 모두 반영. 기술 분석 위주, 외부 데이터 0이면 무시
    # (1단계까지는 +4/-3, 2단계 지표 추가로 ±점수 폭이 커져서 +6/-5로 조정)
    opinion = "매수" if score >= 6 else "매도" if score <= -5 else "중립"
    return opinion, score


def assess_cautious_entry(opinion, score, ichimoku, stoch_rsi, divergence,
                          psar, investor, cci, price, pivot):
    """중립 의견에서 '소량 진입 가능' 여부 판정
    조건: 의견=중립 AND 점수 +2~+5 AND 다음 신호 중 2개 이상 매수
      - 일목균형표 매수
      - 스토캐스틱 RSI 매수
      - 강세 다이버전스
      - 파라볼릭 SAR 상승
      - CCI 매수
      - 외국인·기관 수급 매수 우세
    반환: {"entry": True/False, "signals": [...], "reason": "...", "stopLoss": int}"""
    result = {"entry": False, "signals": [], "reason": "", "stopLoss": 0}

    # 중립 의견이 아니거나 점수가 약한 매수 구간(+2~+5)이 아니면 대상 아님
    if opinion != "중립" or score < 2 or score > 5:
        return result

    matched = []
    if ichimoku and ichimoku.get("signal") == "매수":
        matched.append("일목균형표 " + ("강세" if "강한" in ichimoku.get("comment", "") else "매수"))
    if stoch_rsi and stoch_rsi.get("signal") == "매수":
        matched.append("스토캐스틱 RSI 매수")
    if divergence and divergence.get("bullish"):
        matched.append("강세 다이버전스")
    if psar and psar.get("signal") == "매수":
        matched.append("파라볼릭 SAR 상승")
    if cci and cci.get("signal") == "매수":
        matched.append("CCI " + ("극단 과매도" if "극단" in cci.get("comment", "") else "과매도"))
    if investor:
        f = investor.get("foreign", 0)
        inst = investor.get("institution", 0)
        if (f != 0 or inst != 0) and (f > 0 or inst > 0):
            matched.append(f"수급 매수 우세(외국인 {f:+,} / 기관 {inst:+,})")

    # 매도 신호가 동시에 강하게 있으면 제외 (혼조 회피)
    bearish_count = 0
    if ichimoku and ichimoku.get("signal") == "매도": bearish_count += 1
    if stoch_rsi and stoch_rsi.get("signal") == "매도": bearish_count += 1
    if divergence and divergence.get("bearish"): bearish_count += 1
    if psar and psar.get("signal") == "매도": bearish_count += 1

    if len(matched) >= 2 and bearish_count < 2:
        result["entry"] = True
        result["signals"] = matched
        # 손절선: 파라볼릭 SAR 우선, 없으면 S1
        if psar and psar.get("psar"):
            result["stopLoss"] = psar["psar"]
            result["reason"] = f"중립이지만 매수 신호 {len(matched)}개 확인 — 소량 진입 검토 가능"
        elif pivot and pivot.get("s1"):
            result["stopLoss"] = pivot["s1"]
            result["reason"] = f"중립이지만 매수 신호 {len(matched)}개 확인 — 소량 진입 검토 가능"
        else:
            result["stopLoss"] = round(price * 0.95)
            result["reason"] = f"중립이지만 매수 신호 {len(matched)}개 확인 — 소량 진입 검토 가능"
    return result


def price_targets(price, op, rsi, pivot):
    if op == "중립": return {"sp": 0, "sl": "해당없음", "tp": 0, "tp2": 0, "stop": 0}
    if op == "매수":
        tp1   = round(price * (1.12 if rsi and rsi < 35 else 1.08))
        tp2   = round(pivot["r2"]) if pivot else round(price * 1.15)
        stop  = round(pivot["s1"]) if pivot else round(price * 0.94)
        return {"sp": price, "sl": "매수 추천가", "tp": tp1, "tp2": tp2, "stop": stop}
    return {"sp": round(price * 1.02), "sl": "매도 추천가",
            "tp": round(price * 0.92), "tp2": round(price * 0.88), "stop": round(price * 1.05)}


def gen_text(code, op, rsi, wr, mfi, ft, obv, weekly, investor, short, vol_surge, breakout):
    f   = investor.get("foreign", 0) if investor else 0
    inst = investor.get("institution", 0) if investor else 0
    basis = [
        f"RSI {rsi or '-'} · Williams%R {wr or '-'} · MFI {mfi or '-'} — "
        f"{'다중 과매도, 강한 반등 신호' if (rsi and rsi < 30) or (wr and wr < -80) else '다중 과매수, 조정 경계' if (rsi and rsi > 70) or (wr and wr > -20) else '지표 중립권'}",
        f"외국인 {'+' if f > 0 else ''}{f:,}주 · 기관 {'+' if inst > 0 else ''}{inst:,}주 순매수 · OBV {obv['trend'] if obv else '-'} — "
        f"{'외국인·기관 동반 매수, 강한 수급' if f > 0 and inst > 0 else '외국인·기관 동반 매도, 수급 부담' if f < 0 and inst < 0 else '수급 혼조'}",
        f"주봉 {weekly['opinion']} ({weekly['comment']}) · 공매도 {short.get('ratio', 0)}% · "
        f"{'52주 신고가 근접' if breakout.get('nearHigh') else '52주 신저가 근접' if breakout.get('nearLow') else str(breakout.get('position', 50)) + '%'}",
    ]
    risk_map = {
        "000660": ["HBM 고객사 발주 지연 및 경쟁사 추격", "미중 수출규제 강화 시 공급망 차질", "원달러 급변동 시 환차손"],
        "005930": ["파운드리 TSMC와 기술 격차", "스마트폰 수요 회복 지연", "IT 투자 사이클 하강"],
        "066570": ["가전 수요 부진 및 中 업체 경쟁", "전장 EV 수요 둔화", "원자재·물류비 상승"],
        "009150": ["IT 수요 둔화로 MLCC 단가 하락", "삼성전자 의존도 높아 수주 변동성 존재", "중국 경쟁사 저가 공세"],
        "005380": ["미국·유럽 전기차 수요 둔화", "미국 관세 부과 시 수익성 악화", "원화 강세 시 수출 경쟁력 저하"],
    }
    notes = (
        ["피봇 S1 지지 확인 후 분할 매수", "OBV·외국인 수급 지속 확인", "주봉 신호와 일봉 일치 시 비중 확대"] if op == "매수" else
        ["피봇 R1 저항 확인 후 분할 매도", "공매도 비율 상승 시 매도 강화", "주봉 데드크로스 확인 후 본격 매도"] if op == "매도" else
        ["주봉·일봉 동시 매수 신호 확인 후 진입", "외국인 순매수 전환 시 진입 검토", "공매도 감소 + 거래량 증가 조합 주시"]
    )
    return basis, risk_map.get(code, risk_map["000660"]), notes


# ─────────────────────────────────────────
# 종목 분석 메인
# ─────────────────────────────────────────
def analyze_stock(stock, kospi):
    code, name = stock["code"], stock["name"]
    print(f"\n▶ {name} ({code}) 분석 중...", file=sys.stderr)

    # 데이터 수집
    # 가격 우선순위: 실전 KIS > 네이버 > 모의 KIS
    #   - 실전 KIS: NXT 포함 통합시세 + 실시간
    #   - 네이버: NXT 포함 통합시세 + 실시간 (모의 KIS보다 정확)
    #   - 모의 KIS: NXT 미반영 + 지연 → 마지막 폴백
    is_real_kis = KIS_AVAILABLE and "openapivts" not in KIS_BASE_URL
    if is_real_kis:
        # 실전 키: KIS 우선
        kis_price = fetch_kis_price(code)
        naver = kis_price or fetch_naver_price(code)
    else:
        # 모의 키 또는 KIS 미사용: 네이버 우선
        naver = fetch_naver_price(code)
        if not naver and KIS_AVAILABLE:
            naver = fetch_kis_price(code)

    kis_inv   = fetch_kis_investor(code) if KIS_AVAILABLE else None
    investor  = kis_inv or fetch_investor_flow(code) or {
        "foreign": 0, "institution": 0, "individual": 0,
        "foreignTrend": "중립", "comment": "수급 데이터 없음"}

    kis_short = fetch_kis_short(code) if KIS_AVAILABLE else None
    short     = kis_short or fetch_short_selling(code) or {
        "ratio": 0, "volume": 0, "comment": "공매도 데이터 없음"}
    news     = []  # 뉴스 제거 (해외서버 RSS 차단 - DART 공시로 대체)
    dart     = fetch_dart(code)
    time.sleep(0.1)

    meta_d, candles_d = fetch_yahoo_ohlcv(stock["yf"], "1d", "60d")
    meta_w, candles_w = fetch_yahoo_ohlcv(stock["yf"], "1wk", "1y")
    meta_m, candles_m = fetch_yahoo_ohlcv(stock["yf"], "1mo", "2y")  # 2년으로 단축

    closes_d  = [c["close"]  for c in candles_d]
    highs_d   = [c["high"]   for c in candles_d]
    lows_d    = [c["low"]    for c in candles_d]
    opens_d   = [c["open"]   for c in candles_d]
    volumes_d = [c["volume"] for c in candles_d]
    has_data  = len(closes_d) >= 10

    # 가격
    price   = naver["price"]    if naver else round(meta_d.get("regularMarketPrice", 0))
    prev    = naver["prevClose"] if naver else round(meta_d.get("previousClose", 0))
    high52w = naver["high52w"]  if naver else round(meta_d.get("fiftyTwoWeekHigh", 0))
    low52w  = naver["low52w"]   if naver else round(meta_d.get("fiftyTwoWeekLow", 0))
    source  = naver["source"]   if naver else "Yahoo Finance"

    # 52주 값이 비정상(0이거나 현재가보다 높은 최저가 등)이면 Yahoo로 보강
    yf_high = round(meta_d.get("fiftyTwoWeekHigh", 0))
    yf_low  = round(meta_d.get("fiftyTwoWeekLow", 0))
    if high52w <= 0 or high52w < price:
        high52w = max(yf_high, price)
    if low52w <= 0 or low52w > price:
        low52w = min(yf_low, price) if yf_low > 0 else low52w

    if price == 0:
        return None

    # 목표주가 + 적정주가 (price 정의 후 호출)
    target   = fetch_target_price(code)
    eps, bps = fetch_financial_data(stock["yf"], code)
    fair     = calc_fair_value(code, price, eps, bps)

    # 기술 지표
    rsi   = calc_rsi(closes_d)   if has_data else None
    macd, macd_sig, macd_hist = calc_macd(closes_d) if has_data else (None, None, None)
    stoch = calc_stoch(highs_d, lows_d, closes_d) if has_data else None
    stoch_rsi = calc_stoch_rsi(closes_d) if has_data else None
    divergence = detect_divergence(closes_d, highs_d, lows_d) if has_data else None
    ichimoku = calc_ichimoku(highs_d, lows_d, closes_d) if has_data else None
    cci = calc_cci(highs_d, lows_d, closes_d) if has_data else None
    psar = calc_psar(highs_d, lows_d, closes_d) if has_data else None
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

    # 고급 분석
    weekly   = calc_weekly_signal(candles_w)
    monthly  = calc_weekly_signal(candles_m)  # 월봉도 동일 로직 적용
    monthly["timeframe"] = "월봉"
    rs       = calc_relative_strength(
        round((price - prev) / prev * 100, 2) if prev else 0,
        kospi.get("changePct", 0)
    )
    breakout = check_52w_breakout(price, high52w, low52w)
    vol_surge = check_volume_surge(volumes_d) if has_data else {"surge": False, "ratio": 1.0, "comment": ""}

    avg_vol   = sum(volumes_d[-20:]) / min(20, len(volumes_d)) if has_data and volumes_d else 0
    vol_ratio = volumes_d[-1] / avg_vol if avg_vol > 0 else 1
    fg        = fear_greed(rsi, stoch, obv["slope"] if obv else 0, adx["adx"] if adx else None, vol_ratio)

    ft = investor.get("foreignTrend", "중립") if investor else "중립"
    fc = investor.get("comment", "") if investor else ""

    contra = contra_signal(rsi, macd, macd_sig, obv, pats, fg["score"], investor)
    opinion, score = master_signal(
        rsi, macd, macd_sig, stoch, wr, mfi, adx, obv,
        closes_d, price, high52w, low52w, vwap,
        weekly["opinion"], investor, short.get("ratio", 0), news,
        stoch_rsi, divergence,
        ichimoku, cci, psar, value_surge
    )
    cautious = assess_cautious_entry(opinion, score, ichimoku, stoch_rsi,
                                      divergence, psar, investor, cci, price, pivot)
    pt = price_targets(price, opinion, rsi or 50, pivot)
    basis, risk, notes = gen_text(code, opinion, rsi, wr, mfi, ft, obv,
                                   weekly, investor, short, vol_surge, breakout)

    def cmt_rsi(v):
        if v is None: return "데이터 부족"
        return ("강한 과매도 — 반등 가능" if v < 30 else "저점권 접근" if v < 45 else
                "강한 과매수 — 조정 경계" if v > 70 else "과매수 진입" if v > 60 else "중립 구간")

    # 볼린저 밴드 위치
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
        "tradedAt": naver.get("tradedAt", "") if naver else "",
        "targetPrice_consensus": target.get("consensus", 0),
        "targetPrice_high": target.get("high", 0),
        "targetPrice_low": target.get("low", 0),
        "targetPrice_count": target.get("count", 0),
        "targetPrice_source": target.get("source", ""),
        "fairValue": fair.get("fair_value", 0),
        "fairValueGap": fair.get("gap", 0),
        "fairValueComment": fair.get("gap_comment", ""),
        "fairValueDetail": {
            "per_fair": fair.get("per_fair", 0),
            "pbr_fair": fair.get("pbr_fair", 0),
            "sector": fair.get("sector", ""),
            "sector_per": fair.get("sector_per", 0),
            "sector_pbr": fair.get("sector_pbr", 0),
        },
        "eps": eps or 0,
        "bps": bps or 0,
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
                         "과매도 — 반등 임박" if stoch < 20 else "과매수 — 조정 주의" if stoch > 80 else "중립"),
        "stochRsi": stoch_rsi,
        "divergence": divergence,
        "ichimoku": ichimoku,
        "cci": cci,
        "psar": psar,
        "valueSurge": value_surge,
        "cautiousEntry": cautious,
        "wr": wr, "wrComment": ("데이터 부족" if wr is None else
                                "과매도 — 매수 고려" if wr < -80 else "과매수 — 매도 고려" if wr > -20 else "중립"),
        "mfi": mfi, "mfiComment": ("데이터 부족" if mfi is None else
                                   "거래량 기반 과매도" if mfi < 20 else "거래량 기반 과매수" if mfi > 80 else "중립"),
        "adx": adx, "obv": obv, "vwap": vwap, "pivot": pivot,
        "sma5": sma5, "sma20": sma20, "sma60": sma60,
        "patterns": pats, "contra": contra, "fg": fg,
        "ft": ft, "fc": fc,
        "volRatio": round(vol_ratio, 2),
        "investor": investor,
        "short": short,
        "weekly": weekly,
        "monthly": monthly,
        "relativeStrength": rs,
        "breakout": breakout,
        "volSurge": vol_surge,
        "news": news,
        "dart": dart,
        "basis": basis, "risk": risk, "notes": notes,
        "noChart": not has_data,
    }


def main():
    now = datetime.now(KST)
    print(f"📊 수집 시작: {now.strftime('%Y-%m-%d %H:%M:%S KST')}", file=sys.stderr)

    # KOSPI 지수
    print("▶ KOSPI 지수 수집...", file=sys.stderr)
    kospi = fetch_kospi()
    print(f"  KOSPI: {kospi['price']} ({'+' if kospi['changePct'] >= 0 else ''}{kospi['changePct']}%)", file=sys.stderr)

    stocks_data = []
    for stock in STOCKS:
        try:
            result = analyze_stock(stock, kospi)
            if result:
                stocks_data.append(result)
            else:
                print(f"  ⚠ {stock['name']} 데이터 없음", file=sys.stderr)
        except Exception as e:
            print(f"  ❌ {stock['name']} 오류: {e}", file=sys.stderr)
        time.sleep(0.2)

    output = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "updatedTime": now.strftime("%H:%M"),
        "kospi": kospi,
        "stocks": stocks_data,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ data.json 완료 — {len(stocks_data)}개 종목", file=sys.stderr)


if __name__ == "__main__":
    main()
