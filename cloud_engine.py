

import requests
from bs4 import BeautifulSoup
import re

TARGET_STOCKS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "066570": "LG전자"
}

def get_live_price(code):
    url = f"https://naver.com{code}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, 'html.parser')
        blind_val = soup.select_one(".no_today .blind")
        if blind_val:
            return blind_val.text.strip()
    except:
        pass
    return "0"

def update_html():
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()

    for code, name in TARGET_STOCKS.items():
        live_price = get_live_price(code)
        html_content = re.sub(
            rf'({name}</div>.*?acc-val">)', 
            f'\\1현재가 {live_price}원 | ', 
            html_content
        )
        print(f"📊 {name} 실시간 데이터 주입 완료: {live_price}원")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

if __name__ == "__main__":
    print("🚀 깃허브 가상 서버: 주가 수집 및 HTML 결합 시작")
    update_html()
    print("✅ 데이터 동기화 완료")