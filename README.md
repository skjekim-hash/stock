# 📊 국내 주식 AI 분석 앱

SK하이닉스 · 삼성전자 · LG전자 실시간 분석  
GitHub Actions가 5분마다 자동으로 주가를 수집합니다.

---

## 🚀 설치 방법 (5분이면 완료!)

### 1단계 — 저장소 만들기
1. GitHub 로그인
2. 우측 상단 `+` → **New repository**
3. Repository name: `stock-analyzer` (원하는 이름)
4. **Public** 선택 ✅ (Pages 무료 사용)
5. **Create repository** 클릭

### 2단계 — 파일 업로드
1. 저장소 메인 페이지에서 **Add file → Upload files** 클릭
2. 아래 파일들을 드래그 앤 드롭:
   - `index.html`
   - `scripts/fetch_stocks.py`
   - `.github/workflows/fetch.yml`
3. **Commit changes** 클릭

> ⚠️ `.github/workflows/` 폴더 구조가 유지되어야 합니다!

### 3단계 — GitHub Pages 활성화
1. 저장소 상단 **Settings** 탭
2. 왼쪽 메뉴 **Pages**
3. Source: **Deploy from a branch**
4. Branch: **main** / **/ (root)** 선택
5. **Save**
6. 잠시 후 `https://[내아이디].github.io/stock-analyzer/` 접속 가능!

### 4단계 — Actions 권한 설정
1. 저장소 **Settings → Actions → General**
2. **Workflow permissions** → **Read and write permissions** 선택
3. **Save**

### 5단계 — 첫 번째 수동 실행
1. 저장소 상단 **Actions** 탭
2. 왼쪽 **주식 데이터 자동 수집** 클릭
3. **Run workflow** → **Run workflow** 버튼 클릭
4. 초록 체크 뜨면 완료!

---

## 📱 폰에서 사용하기

1. Chrome에서 `https://[내아이디].github.io/stock-analyzer/` 접속
2. 우측 상단 메뉴 → **홈 화면에 추가**
3. 앱처럼 바탕화면에서 실행 가능!

---

## ⚙️ 자동 실행 스케줄

| 시간 (KST) | 실행 간격 |
|-----------|---------|
| 07:30 ~ 09:00 | 10분마다 |
| 09:00 ~ 15:30 | **5분마다** |
| 15:30 이후 | 실행 안 함 |

---

## 📊 분석 항목

- **실시간 주가** — 네이버 금융 (국내 주식 전용)
- **기술 지표** — RSI, MACD, 스토캐스틱, Williams %R, MFI, ADX, OBV, VWAP, 볼린저
- **캔들 패턴** — 도지, 망치형, 역망치, 유성형, 강세/약세 장악형, 장대양봉/음봉
- **역발상 신호** — AI 군중 신호를 역이용하는 차별화된 분석
- **공포·탐욕 지수** — 시장 심리 정량화
- **피봇 포인트** — 지지·저항선 자동 계산

---

## 🆓 비용

완전 무료!
- GitHub 무료 계정: Actions 월 2,000분 제공
- 평일 장중 5분 간격 실행: 월 약 350분 사용 (여유 있음)

---

## ❓ 문제 해결

**data.json이 없다고 나올 때**  
→ Actions 탭에서 수동으로 `Run workflow` 실행

**Actions가 실패할 때**  
→ Settings → Actions → General → Workflow permissions → Read and write 확인

**주가가 안 나올 때**  
→ 네이버 금융 API 일시적 오류일 수 있음, 잠시 후 자동 재시도
