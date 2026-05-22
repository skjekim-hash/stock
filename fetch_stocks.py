#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
국내 주식 데이터 수집 + 기술 지표 계산 스크립트
GitHub Actions에서 실행 → data.json 생성
서버에서 직접 호출하므로 CORS 문제 없음!
"""

import json
import sys
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

# ─────────────────────────────────────────
# 종목 설정
# ─────────────────────────────────────────
STOCKS = [
    {"code": "000660", "yf": "000660.KS", "name": "SK하이닉스", "emoji": "🔵"},
    {"code": "005930", "yf": "005930.KS", "name": "삼성전자",   "emoji": "🟡"},
    {"code": "066570", "yf": "066570.KS", "name": "LG전자",     "emoji": "🔴"},
]

KST = timezone(timedelta(hours=9))


def http_get_json(url, timeout=15):
    """간단한 GET 요청 (서버 환경이라 CORS 없음)"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ─────────────────────────────────────────
# 네이버 금융 현재가
# ─────────────────────────────────────────
def fetch_naver_price(code):
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/basic"
        d = http_get_json(url)

        def to_n(v):
            if v is None:
                return 0
            try:
                return float(str(v).replace(",", ""))
            except (ValueError, TypeError):
                return 0

        price = to_n(d.get("closePrice")) or to_n(d.get("currentPrice"))
        if price > 0:
            return {
                "price": round(price),
                "prevClose": round(to_n(d.get("compareToPreviousClosePrice", 0))),
                "high52w": round(to_n(d.get("highPrice", 0)) or to_n(d.get("yearHighPrice", 0))),
                "low52w": round(to_n(d.get("lowPrice", 0)) or to_n(d.get("yearLowPrice", 0))),
                "source": "네이버 금융",
            }
    except Exception as e:
        print(f"  네이버 실패 ({code}): {e}", file=sys.stderr)
    return None


# ─────────────────────────────────────────
# Yahoo Finance 60일 OHLCV
# ─────────────────────────────────────────
def fetch_yahoo_ohlcv(yf_sym):
    for base in ["query1", "query2"]:
        try:
            url = (f"https://{base}.finance.yahoo.com/v8/finance/chart/{yf_sym}"
                   f"?interval=1d&range=60d&includePrePost=false")
            d = http_get_json(url)
            result = d.get("chart", {}).get("result", [None])[0]
            if not result:
                continue
            meta = result.get("meta", {})
            ts = result.get("timestamp", []) or []
            q = result.get("indicators", {}).get("quote", [{}])[0]
            candles = []
            for i, t in enumerate(ts):
                close = q.get("close", [None] * len(ts))[i]
                if close is None:
                    continue
                candles.append({
                    "open": q.get("open", [0] * len(ts))[i] or 0,
                    "high": q.get("high", [0] * len(ts))[i] or 0,
                    "low": q.get("low", [0] * len(ts))[i] or 0,
                    "close": close,
                    "volume": q.get("volume", [0] * len(ts))[i] or 0,
                })
            if len(candles) >= 10:
                return meta, candles
        except Exception as e:
            print(f"  Yahoo 실패 ({yf_sym}/{base}): {e}", file=sys.stderr)
    return {}, []


# ─────────────────────────────────────────
# 기술 지표 계산
# ─────────────────────────────────────────
def ema(arr, p):
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
        if d > 0:
            ag += d
        else:
            al -= d
    ag /= p
    al /= p
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
    m, s = round(ml[-1], 2), round(sig[-1], 2)
    return m, s, round(m - s, 2)


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
        h, l, ph, pl, pc = highs[i], lows[i], highs[i - 1], lows[i - 1], closes[i - 1]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        pdm.append(max(h - ph, 0) if max(h - ph, 0) > (pl - l) else 0)
        ndm.append(max(pl - l, 0) if max(pl - l, 0) > (h - ph) else 0)
    atr = sum(tr[-p:]) / p
    if atr == 0:
        return None
    pdi = sum(pdm[-p:]) / p / atr * 100
    ndi = sum(ndm[-p:]) / p / atr * 100
    dx = 0 if (pdi + ndi) == 0 else abs(pdi - ndi) / (pdi + ndi) * 100
    strength = "강한 추세" if dx > 25 else "약한 추세" if dx > 20 else "추세 없음(횡보)"
    return {"adx": round(dx, 1), "pdi": round(pdi, 1), "ndi": round(ndi, 1), "strength": strength}


def calc_mfi(highs, lows, closes, volumes, p=14):
    if len(closes) < p + 1:
        return None
    pmf = nmf = 0
    for i in range(len(closes) - p, len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        ptp = (highs[i - 1] + lows[i - 1] + closes[i - 1]) / 3
        mf = tp * volumes[i]
        if tp > ptp:
            pmf += mf
        else:
            nmf += mf
    return 100.0 if nmf == 0 else round(100 - 100 / (1 + pmf / nmf), 1)


def calc_vwap(highs, lows, closes, volumes, p=5):
    if len(closes) < p:
        return None
    tv = sv = 0
    for i in range(-p, 0):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        tv += tp * volumes[i]
        sv += volumes[i]
    return None if sv == 0 else round(tv / sv)


def calc_pivot(highs, lows, closes):
    if len(closes) < 2:
        return None
    h, l, c = highs[-2], lows[-2], closes[-2]
    p = (h + l + c) / 3
    return {
        "p": round(p), "r1": round(2 * p - l), "r2": round(p + (h - l)),
        "s1": round(2 * p - h), "s2": round(p - (h - l)),
    }


def calc_obv(closes, volumes):
    if len(closes) < 2:
        return {"obv": 0, "slope": 0, "trend": "횡보"}
    obv = 0
    arr = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
        arr.append(obv)
    recent = arr[-5:]
    base = recent[0] if recent[0] != 0 else 1
    slope = (recent[-1] - recent[0]) / abs(base) * 100 if base != 0 else 0
    trend = "상승" if slope > 1 else "하락" if slope < -1 else "횡보"
    return {"obv": arr[-1], "slope": round(slope, 1), "trend": trend}


def detect_patterns(opens, highs, lows, closes):
    pat = []
    if len(closes) < 3:
        return pat
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    po, pc = opens[-2], closes[-2]
    body, rng = abs(c - o), h - l
    if rng <= 0:
        return pat
    if body < rng * 0.1:
        pat.append({"name": "도지", "type": "반전경고", "color": "#ffd166"})
    if c > o and (o - l) > body * 2 and (h - c) < body * 0.5:
        pat.append({"name": "망치형", "type": "상승반전", "color": "#00e676"})
    if c > o and (h - c) > body * 2 and (o - l) < body * 0.5:
        pat.append({"name": "역망치", "type": "상승반전", "color": "#00e676"})
    if c < o and (h - o) > body * 2 and (c - l) < body * 0.5:
        pat.append({"name": "유성형", "type": "하락반전", "color": "#ff4d6d"})
    if pc > po and c < o and o < pc and c > po:
        pat.append({"name": "강세장악", "type": "상승반전", "color": "#00e676"})
    if po > pc and o < c and c < po and o > pc:
        pat.append({"name": "약세장악", "type": "하락반전", "color": "#ff4d6d"})
    if c > o and body > rng * 0.7:
        pat.append({"name": "장대양봉", "type": "강한상승", "color": "#00e676"})
    if o > c and body > rng * 0.7:
        pat.append({"name": "장대음봉", "type": "강한하락", "color": "#ff4d6d"})
    return pat[:3]


def fear_greed(rsi, stoch, obv_slope, adx_val, vol_ratio):
    score = 50
    if rsi is not None:
        score += (rsi - 50) * 0.4
    if stoch is not None:
        score += (stoch - 50) * 0.2
    if obv_slope is not None:
        score += obv_slope * 0.5
    if adx_val is not None:
        score += 0 if adx_val > 25 else -5
    if vol_ratio is not None:
        score += 5 if vol_ratio > 1.2 else (-5 if vol_ratio < 0.8 else 0)
    score = min(max(round(score), 0), 100)
    label = ("극단적 탐욕" if score >= 70 else "탐욕" if score >= 55
             else "중립" if score >= 45 else "공포" if score >= 30 else "극단적 공포")
    color = ("#ff4d6d" if score >= 70 else "#ffd166" if score >= 55
             else "#9ab" if score >= 45 else "#ffd166" if score >= 30 else "#00e676")
    return {"score": score, "label": label, "color": color}


def contra_signal(rsi, macd, macd_sig, obv, patterns, fg_score):
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
    if macd is not None and macd_sig is not None:
        if macd > macd_sig and rsi and rsi > 70:
            signals.append("⚠️ MACD 상승+RSI 과매수 → AI 매수신호 과잉 주의")
        if macd < macd_sig and rsi and rsi < 30:
            signals.append("⚠️ MACD 하락+RSI 과매도 → AI 매도신호 과잉 주의")
    bull = len([p for p in patterns if "상승" in p["type"]])
    bear = len([p for p in patterns if "하락" in p["type"]])
    if bull >= 2:
        signals.append("⚠️ 상승 패턴 다수 → 차익실현 주의")
        strength -= 1
    if bear >= 2:
        signals.append("⚠️ 하락 패턴 다수 → 역매수 기회 탐색")
        strength += 1
    action = ("역발상 매수 기회" if strength >= 2 else
              "역발상 매도 기회" if strength <= -2 else "현 신호 유효")
    return {"signals": signals[:3], "action": action, "strength": strength}


def master_signal(rsi, macd, macd_sig, stoch, wr, mfi, adx, obv, closes, price, h52, l52, vwap):
    score = 0
    if rsi is not None:
        score += 2 if rsi < 30 else 1 if rsi < 45 else -2 if rsi > 70 else -1 if rsi > 60 else 0
    if macd is not None and macd_sig is not None:
        score += 2 if macd > macd_sig else -2 if macd < macd_sig else 0
    if wr is not None:
        score += 1 if wr < -80 else -1 if wr > -20 else 0
    if stoch is not None:
        score += 1 if stoch < 20 else -1 if stoch > 80 else 0
    if mfi is not None:
        score += 1 if mfi < 20 else -1 if mfi > 80 else 0
    if obv and obv["slope"] is not None:
        score += 1 if obv["slope"] > 2 else -1 if obv["slope"] < -2 else 0
    if adx and adx["adx"] is not None and adx["adx"] < 15:
        score = round(score * 0.7)
    if h52 > 0 and l52 > 0:
        pos = (price - l52) / (h52 - l52) * 100
        score += 1 if pos < 20 else -1 if pos > 85 else 0
    if vwap:
        score += 1 if price > vwap else -1
    s5, s20 = calc_sma(closes, 5), calc_sma(closes, 20)
    if s5 and s20:
        score += 1 if s5 > s20 else -1
    opinion = "매수" if score >= 4 else "매도" if score <= -3 else "중립"
    return opinion, score


def price_targets(price, op, rsi, pivot):
    if op == "중립":
        return {"sp": 0, "sl": "해당없음", "tp": 0, "tp2": 0, "stop": 0}
    if op == "매수":
        tp1 = round(price * (1.12 if rsi and rsi < 35 else 1.08))
        tp2 = round(pivot["r2"]) if pivot else round(price * 1.15)
        stop = round(pivot["s1"]) if pivot else round(price * 0.94)
        return {"sp": price, "sl": "매수 추천가", "tp": tp1, "tp2": tp2, "stop": stop}
    return {"sp": round(price * 1.02), "sl": "매도 추천가",
            "tp": round(price * 0.92), "tp2": round(price * 0.88), "stop": round(price * 1.05)}


def gen_text(code, op, rsi, macd, macd_sig, wr, mfi, ft, obv):
    rsi_s = rsi if rsi is not None else "-"
    wr_s = wr if wr is not None else "-"
    mfi_s = mfi if mfi is not None else "-"
    obv_trend = obv["trend"] if obv else "-"
    obv_slope = obv["slope"] if obv else 0

    over_sold = (rsi is not None and rsi < 30) or (wr is not None and wr < -80)
    over_bought = (rsi is not None and rsi > 70) or (wr is not None and wr > -20)
    momentum = "다중 과매도 동시 확인, 강한 반등 신호" if over_sold else \
               "다중 과매수 동시 확인, 조정 경계" if over_bought else "지표 중립권, 방향성 대기"

    flow = ("거래량·수급 동반 상승, 강한 매수" if obv_trend == "상승" and ft == "매수우세" else
            "거래량·수급 동반 하락, 강한 매도" if obv_trend == "하락" and ft == "매도우세" else
            "거래량 수급 혼조, 추세 전환 확인 필요")

    news = {
        "000660": "HBM 3세대 양산 확대 및 엔비디아·AMD AI 반도체 수요 증가 지속. 미중 수출규제·환율 변수 주시",
        "005930": "파운드리 2나노 공정 수주 및 HBM4 개발 경쟁. 스마트폰 수요 회복과 AI 반도체 수주 동향 핵심",
        "066570": "전장(VS사업부) 수주 확대 및 HVAC·전기차 부품 성장성. 가전 수요 회복과 신흥시장 확대 여부 주목",
    }
    basis = [
        f"RSI {rsi_s} · Williams%R {wr_s} · MFI {mfi_s} — {momentum}",
        f"OBV {obv_trend} ({'+' if obv_slope > 0 else ''}{obv_slope}%) · 수급 {ft} — {flow}",
        news.get(code, news["000660"]),
    ]
    risk_map = {
        "000660": ["HBM 고객사 발주 지연 및 삼성·마이크론 경쟁 심화",
                   "미국 대중 수출규제 강화 시 공급망 차질 위험",
                   "원달러 1400원 이상 지속 시 환차손 확대"],
        "005930": ["파운드리 TSMC와 기술 격차 지속으로 수주 경쟁 열위",
                   "갤럭시 판매 부진 및 스마트폰 수요 회복 지연",
                   "글로벌 IT 투자 사이클 하강 시 실적 하향"],
        "066570": ["가전 글로벌 수요 부진 및 中 업체 가격 경쟁",
                   "전장 부품 EV 수요 둔화로 수주 목표 미달",
                   "원자재·물류비 상승으로 마진 압박"],
    }
    notes_map = {
        "매수": ["피봇 S1 지지 확인 후 1/3 분할 매수 권장",
                "OBV 상승 지속 여부로 세력 이탈 모니터링",
                "목표가 R1 돌파 시 R2까지 추가 보유 전략"],
        "매도": ["피봇 R1 저항 확인 후 분할 매도 권장",
                "OBV 하락 반전 시 즉시 손절 실행",
                "Williams %R -20 이상 유지 시 매도 지속"],
        "중립": ["ADX 20 이상 + MACD 방향 전환 확인 후 진입",
                "현금 비중 유지하며 OBV 추세 모니터링",
                "공포탐욕 극단값 진입 시 역발상 매매 준비"],
    }
    return basis, risk_map.get(code, risk_map["000660"]), notes_map.get(op, notes_map["중립"])


# ─────────────────────────────────────────
# 종목 분석
# ─────────────────────────────────────────
def analyze_stock(stock):
    code = stock["code"]
    print(f"▶ {stock['name']} ({code}) 분석 중...", file=sys.stderr)

    naver = fetch_naver_price(code)
    meta, candles = fetch_yahoo_ohlcv(stock["yf"])

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    opens = [c["open"] for c in candles]
    volumes = [c["volume"] for c in candles]
    has_data = len(closes) >= 10

    price = (naver["price"] if naver else
             round(meta.get("regularMarketPrice", 0)) or
             (round(closes[-1]) if closes else 0))
    prev = (naver["prevClose"] if naver else
            round(meta.get("previousClose", 0)) or
            (round(closes[-2]) if len(closes) > 1 else price))
    high52w = (naver["high52w"] if naver else round(meta.get("fiftyTwoWeekHigh", 0)))
    low52w = (naver["low52w"] if naver else round(meta.get("fiftyTwoWeekLow", 0)))
    source = naver["source"] if naver else "Yahoo Finance"

    if price == 0:
        return None

    rsi = calc_rsi(closes) if has_data else None
    macd, macd_sig, macd_hist = calc_macd(closes) if has_data else (None, None, None)
    stoch = calc_stoch(highs, lows, closes) if has_data else None
    wr = calc_williams(highs, lows, closes) if has_data else None
    adx = calc_adx(highs, lows, closes) if has_data else None
    mfi = calc_mfi(highs, lows, closes, volumes) if has_data else None
    obv = calc_obv(closes, volumes) if has_data else {"obv": 0, "slope": 0, "trend": "횡보"}
    vwap = calc_vwap(highs, lows, closes, volumes) if has_data else None
    pivot = calc_pivot(highs, lows, closes) if has_data else None
    sma5 = calc_sma(closes, 5) if has_data else None
    sma20 = calc_sma(closes, 20) if has_data else None
    patterns = detect_patterns(opens, highs, lows, closes) if has_data else []

    avg_vol = sum(volumes[-20:]) / min(20, len(volumes)) if has_data and volumes else 0
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1
    fg = fear_greed(rsi, stoch, obv["slope"], adx["adx"] if adx else None, vol_ratio)

    price_up = closes[-1] > closes[-5] if has_data and len(closes) >= 5 else False
    vol_up = volumes[-1] > avg_vol * 1.1 if has_data else False
    ft = "매수우세" if price_up and vol_up else "매도우세" if (not price_up and vol_up) else "중립"
    fc = ("거래량 증가 속 주가 상승. 외국인·기관 순매수 추정." if ft == "매수우세" else
          "거래량 증가 속 주가 하락. 외국인 매도 압력 우세." if ft == "매도우세" else
          "거래량 평이, 관망세. 방향성 확인 후 접근 권장.")

    contra = contra_signal(rsi, macd, macd_sig, obv, patterns, fg["score"])
    if has_data:
        opinion, score = master_signal(rsi, macd, macd_sig, stoch, wr, mfi, adx,
                                        obv, closes, price, high52w, low52w, vwap)
    else:
        opinion, score = "중립", 0
    pt = price_targets(price, opinion, rsi or 50, pivot)
    basis, risk, notes = gen_text(code, opinion, rsi, macd, macd_sig, wr, mfi, ft, obv)

    def cmt_rsi(v):
        if v is None: return "데이터 부족"
        return ("강한 과매도 — 반등 가능" if v < 30 else "저점권 접근" if v < 45 else
                "강한 과매수 — 조정 경계" if v > 70 else "과매수 진입" if v > 60 else "중립 구간")

    return {
        "code": code, "price": price, "change": price - prev,
        "changePct": round((price - prev) / prev * 100, 2) if prev else 0,
        "high52w": high52w, "low52w": low52w, "opinion": opinion, "score": score, "source": source,
        "suggestedPrice": pt["sp"], "suggestedLabel": pt["sl"],
        "targetPrice": pt["tp"], "targetPrice2": pt["tp2"], "stopLoss": pt["stop"],
        "rsi": rsi, "rsiComment": cmt_rsi(rsi),
        "macd": macd or 0, "macdSignal": macd_sig or 0, "macdHist": macd_hist or 0,
        "macdComment": ("데이터 부족" if macd is None else
                        "골든크로스 — 상승 모멘텀" if macd > macd_sig else "데드크로스 — 하락 압력"),
        "stoch": stoch if stoch is not None else 50,
        "stochComment": ("데이터 부족" if stoch is None else
                         "과매도 — 반등 임박" if stoch < 20 else "과매수 — 조정 주의" if stoch > 80 else "중립 구간"),
        "wr": wr, "wrComment": ("데이터 부족" if wr is None else
                                "과매도(-80↓) — 매수 고려" if wr < -80 else
                                "과매수(-20↑) — 매도 고려" if wr > -20 else "중립 구간"),
        "mfi": mfi, "mfiComment": ("데이터 부족" if mfi is None else
                                   "거래량 기반 과매도" if mfi < 20 else
                                   "거래량 기반 과매수" if mfi > 80 else "중립"),
        "adx": adx, "obv": obv, "vwap": vwap, "pivot": pivot,
        "sma5": sma5, "sma20": sma20, "patterns": patterns,
        "contra": contra, "fg": fg, "ft": ft, "fc": fc,
        "volRatio": round(vol_ratio, 2), "basis": basis, "risk": risk, "notes": notes,
        "noChart": not has_data,
    }


def main():
    now = datetime.now(KST)
    stocks_data = []
    for stock in STOCKS:
        try:
            result = analyze_stock(stock)
            if result:
                stocks_data.append(result)
            else:
                print(f"  ⚠ {stock['name']} 데이터 없음", file=sys.stderr)
        except Exception as e:
            print(f"  ❌ {stock['name']} 오류: {e}", file=sys.stderr)

    output = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "updatedTime": now.strftime("%H:%M"),
        "stocks": stocks_data,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ data.json 생성 완료 — {len(stocks_data)}개 종목", file=sys.stderr)


if __name__ == "__main__":
    main()
