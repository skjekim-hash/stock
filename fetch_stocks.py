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

STOCKS = [
    {"code": "000660", "yf": "000660.KS", "name": "SK하이닉스", "emoji": "🔵"},
    {"code": "005930", "yf": "005930.KS", "name": "삼성전자",   "emoji": "🟡"},
    {"code": "066570", "yf": "066570.KS", "name": "LG전자",     "emoji": "🔴"},
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
        html = http_get(
            f"https://finance.naver.com/item/frgn.naver?code={code}",
            headers={"Referer": "https://finance.naver.com/", "Accept-Language": "ko-KR"}
        )
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
    result = {"consensus": 0, "high": 0, "low": 0, "avg": 0,
              "count": 0, "comment": "", "source": ""}
    try:
        import re
        # 네이버 금융 컨센서스 페이지
        html = http_get(
            f"https://finance.naver.com/item/analyst.naver?code={code}",
            timeout=6,
            headers={"Referer": "https://finance.naver.com/", "Accept-Language": "ko-KR"}
        )
        # 목표주가 파싱
        targets = re.findall(r'목표주가[^0-9]*([0-9,]+)', html)
        prices = [int(p.replace(",", "")) for p in targets if int(p.replace(",", "")) > 0]
        if prices:
            result["consensus"] = round(sum(prices) / len(prices))
            result["high"]      = max(prices)
            result["low"]       = min(prices)
            result["avg"]       = result["consensus"]
            result["count"]     = len(prices)
            result["source"]    = "네이버 금융 컨센서스"
    except Exception as e:
        print(f"  목표주가 HTML 실패 ({code}): {e}", file=sys.stderr)

    # Yahoo Finance 폴백
    if result["consensus"] == 0:
        try:
            d = http_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.KS")
            meta = d.get("chart", {}).get("result", [{}])[0].get("meta", {})
            tp = meta.get("targetMeanPrice") or meta.get("fiftyTwoWeekHigh", 0)
            if tp:
                result["consensus"] = round(float(tp))
                result["source"]    = "Yahoo Finance"
        except: pass

    return result


def calc_fair_value(code, price, eps=None, bps=None, growth=None):
    """PER·PBR 기반 적정주가 계산"""
    # 종목별 업종 평균 PER/PBR (2026 기준)
    sector_data = {
        "000660": {"per": 12, "pbr": 1.8, "name": "반도체"},   # SK하이닉스
        "005930": {"per": 14, "pbr": 1.5, "name": "반도체"},   # 삼성전자
        "066570": {"per": 10, "pbr": 0.9, "name": "가전/전장"}, # LG전자
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

    # RSS 소스 (빠른 것 우선 2개만)
    rss_sources = [
        # Yahoo Finance (가장 빠르고 안정적)
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={code}.KS&region=KR&lang=ko-KR",
        # 네이버 뉴스
        f"https://news.naver.com/main/rss/searchRss.naver?query={name}",
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
        except Exception as e:
            print(f"  RSS 실패 ({rss_url[-40:]}): {e}", file=sys.stderr)

    return news_list

# ─────────────────────────────────────────
# DART 공시 (OpenDartReader 없이 직접)
# ─────────────────────────────────────────
def fetch_dart(code, limit=3):
    dart_list = []
    try:
        # DART 기업 공시 RSS
        url = f"https://dart.fss.or.kr/api/search.json?stock_code={code}&page_count={limit}&sort=date&sort_mth=desc"
        try:
            d = http_json(url)
        except:
            return []
        items = d.get("list") or []
        for item in items[:limit]:
            title = item.get("report_nm") or ""
            date  = item.get("rcept_dt") or ""
            rcept = item.get("rcept_no") or ""
            if title:
                # 중요 공시 분류
                important_kw = ["실적","분기","연간","배당","유상증자","무상증자","합병","분할","자사주","대규모"]
                is_important = any(k in title for k in important_kw)
                dart_list.append({
                    "title": title[:50],
                    "date": str(date)[:8],
                    "important": is_important,
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}" if rcept else "",
                })
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
                  weekly_opinion, investor, short_ratio, news_list):
    score = 0

    # 기술 지표 (일봉)
    if rsi: score += 2 if rsi < 30 else 1 if rsi < 45 else -2 if rsi > 70 else -1 if rsi > 60 else 0
    if macd and macd_sig: score += 2 if macd > macd_sig else -2 if macd < macd_sig else 0
    if wr: score += 1 if wr < -80 else -1 if wr > -20 else 0
    if stoch: score += 1 if stoch < 20 else -1 if stoch > 80 else 0
    if mfi: score += 1 if mfi < 20 else -1 if mfi > 80 else 0
    if obv: score += 1 if obv["slope"] > 2 else -1 if obv["slope"] < -2 else 0
    if adx and adx["adx"] < 15: score = round(score * 0.7)

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

    # 외국인·기관 수급
    if investor:
        f, inst = investor.get("foreign", 0), investor.get("institution", 0)
        if f > 0 and inst > 0: score += 2
        elif f > 0 or inst > 0: score += 1
        elif f < 0 and inst < 0: score -= 2
        elif f < 0 or inst < 0: score -= 1

    # 공매도
    if short_ratio > 5: score -= 1
    elif short_ratio < 1: score += 1

    # 뉴스 감성
    if news_list:
        pos_count = len([n for n in news_list if n["sentiment"] == "긍정"])
        neg_count = len([n for n in news_list if n["sentiment"] == "부정"])
        if pos_count > neg_count: score += 1
        elif neg_count > pos_count: score -= 1

    opinion = "매수" if score >= 6 else "매도" if score <= -5 else "중립"
    return opinion, score


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
    naver    = fetch_naver_price(code)
    investor = fetch_investor_flow(code)
    short    = fetch_short_selling(code)
    news     = fetch_news(code, name)
    dart     = fetch_dart(code)
    time.sleep(0.1)

    meta_d, candles_d = fetch_yahoo_ohlcv(stock["yf"], "1d", "60d")
    meta_w, candles_w = fetch_yahoo_ohlcv(stock["yf"], "1wk", "1y")
    meta_m, candles_m = fetch_yahoo_ohlcv(stock["yf"], "1mo", "2y")  # 2년으로 단축
    # 목표주가 + 적정주가
    target   = fetch_target_price(code)
    eps, bps = fetch_financial_data(stock["yf"], code)
    fair     = calc_fair_value(code, price, eps, bps)

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

    if price == 0:
        return None

    # 기술 지표
    rsi   = calc_rsi(closes_d)   if has_data else None
    macd, macd_sig, macd_hist = calc_macd(closes_d) if has_data else (None, None, None)
    stoch = calc_stoch(highs_d, lows_d, closes_d) if has_data else None
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
        weekly["opinion"], investor, short.get("ratio", 0), news
    )
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
