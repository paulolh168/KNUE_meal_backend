from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException, Query
from lxml import html


# =========================
# meals-A (기존)
# =========================
BASE_URL_A = "http://rec.knue.ac.kr/pub/admi/admi050701.jsp"

MEAL_XPATHS_A = [
    ("조식", '//*[@id="contents"]/div/div[2]/table/tbody/tr[1]/td'),
    ("중식", '//*[@id="contents"]/div/div[2]/table/tbody/tr[2]/td'),
    ("석식", '//*[@id="contents"]/div/div[2]/table/tbody/tr[3]/td'),
]


def build_url_a(y: int, m: int, d: int) -> str:
    return f"{BASE_URL_A}?year={y}&month={m}&date={d}"


def fetch_html_text(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    # connect/read 타임아웃 분리 권장
    r = requests.get(url, headers=headers, timeout=(3, 10))
    r.raise_for_status()

    # A쪽은 EUC-KR/CP949일 수 있어서 폴백 유지
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return r.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.content.decode("utf-8", errors="replace")


def xpath_without_tbody(xpath: str) -> str:
    return xpath.replace("/tbody", "")


def extract_by_xpath(tree, xpath: str):
    nodes = tree.xpath(xpath)
    if not nodes:
        nodes = tree.xpath(xpath_without_tbody(xpath))
    return nodes


def extract_td_lines_preserve_br(td) -> List[str]:
    """
    <br> 기준 줄바꿈 보존 + tail 포함 (텍스트 손실 방지)
    """
    parts: List[str] = []

    if td.text:
        parts.append(td.text)

    for child in td:
        if isinstance(child.tag, str) and child.tag.lower() == "br":
            parts.append("\n")

        if child.text:
            parts.append(child.text)

        if child.tail:
            parts.append(child.tail)

    text = "".join(parts).replace("\xa0", " ")
    lines = [line.strip() for line in text.split("\n")]
    return [line for line in lines if line]


def parse_page_a(y: int, m: int, d: int) -> Dict[str, List[str]]:
    url = build_url_a(y, m, d)
    html_text = fetch_html_text(url)
    tree = html.fromstring(html_text)

    result: Dict[str, List[str]] = {}

    for meal_name, xp in MEAL_XPATHS_A:
        tds = extract_by_xpath(tree, xp)

        meal_items: List[str] = []
        for td in tds:
            meal_items.extend(extract_td_lines_preserve_br(td))

        result[meal_name] = meal_items

    return result


# =========================
# meals-B (신규)
# =========================
BASE_URL_B = "https://pot.knue.ac.kr/enview/knue/mobileMenu.html"

# 요청 요일 -> DOM id 매핑 (스크린샷 기준)
DAY_TO_DIV_ID = {
    "mon": "mon_list",
    "tue": "tue_list",
    "wed": "wed_list",
    "thu": "thu_list",
    "fri": "fri_list",
    "sat": "sat_list",
    "sun": "sun_list",
}

# B는 <th scope="row">아침</th> ... 구조
B_MEAL_KEYS = ("아침", "점심", "저녁")


def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_text_preserve_br(node) -> str:
    """
    node 내부의 <br>를 '\n'으로 보존해 텍스트 구성
    """
    parts: List[str] = []
    if node.text:
        parts.append(node.text)

    for child in node:
        if isinstance(child.tag, str) and child.tag.lower() == "br":
            parts.append("\n")

        if child.text:
            parts.append(child.text)

        if child.tail:
            parts.append(child.tail)

    return "".join(parts).replace("\xa0", " ").strip()


def parse_b_date_from_h3(h3_text: str) -> Optional[str]:
    """
    예: "교직원 식당 ( 2025년 12월 22일 ) 월요일"
    -> "2025-12-22" 추출
    """
    m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", h3_text)
    if not m:
        return None
    yy, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        _ = date(yy, mm, dd)
    except ValueError:
        return None
    return f"{yy:04d}-{mm:02d}-{dd:02d}"


def parse_page_b(day: str) -> Tuple[Optional[str], Dict[str, List[str]]]:
    """
    day: mon|tue|... 로 요청받고,
    해당 요일 div에서 '교직원 식당' 테이블을 찾아 아침/점심/저녁을 파싱
    """
    if day not in DAY_TO_DIV_ID:
        raise ValueError("day must be one of: mon, tue, wed, thu, fri, sat, sun")

    html_text = fetch_html_text(BASE_URL_B)  # B는 보통 UTF-8이지만 fetch_html_text가 폴백도 처리
    tree = html.fromstring(html_text)

    div_id = DAY_TO_DIV_ID[day]

    # 1) 요일 div 찾기
    day_divs = tree.xpath(f'//div[@id="{div_id}"]')
    if not day_divs:
        raise RuntimeError(f"Cannot find day div: {div_id}")
    day_div = day_divs[0]

    # 2) '교직원 식당' h3 찾기
    h3_nodes = day_div.xpath('.//h3[contains(normalize-space(.), "교직원") and contains(normalize-space(.), "식당")]')
    if not h3_nodes:
        # 페이지 구조 변경 가능성
        raise RuntimeError("Cannot find '교직원 식당' section (h3).")

    h3_text = normalize_space(h3_nodes[0].text_content())
    parsed_date = parse_b_date_from_h3(h3_text)

    # 3) h3 아래(같은 div 내부)에서 첫 번째 table(tbl_4)을 우선 사용
    #    (스크린샷 기준: <table class="tbl_4">)
    tables = day_div.xpath('.//table[contains(@class,"tbl_4")]')
    if not tables:
        # table class가 바뀌면 여기서 대응 필요
        tables = day_div.xpath(".//table")
    if not tables:
        raise RuntimeError("Cannot find menu table in the '교직원 식당' section.")

    table = tables[0]

    # 4) 행 파싱: <tr><th scope="row">점심</th><td>...</td></tr>
    out: Dict[str, List[str]] = {k: [] for k in B_MEAL_KEYS}
    rows = table.xpath(".//tr")
    for tr in rows:
        ths = tr.xpath("./th")
        tds = tr.xpath("./td")
        if not ths or not tds:
            continue

        key = normalize_space(ths[0].text_content())
        if key not in out:
            continue

        td_text = extract_text_preserve_br(tds[0])
        # td_text는 "[11:00~14:00] ... \n ..." 형태가 될 수 있음
        lines = [line.strip() for line in td_text.split("\n") if line.strip()]

        # 빈 칸(<td></td>)이면 빈 리스트 유지
        out[key] = lines

    return parsed_date, out


# =========================
# FastAPI
# =========================
app = FastAPI(
    title="KNUE Meal API",
    version="1.1.0",
    description="A: 사도교육원 식단 / B: pot.knue 교직원식당(요일별) 식단",
)


@app.get("/health")
def health():
    return {"ok": True}


# ---- meals-A (기존 엔드포인트 유지) ----
@app.get("/meals-a")
def get_meals_a(
    y: int = Query(..., ge=2000, le=2100, description="연도 (예: 2025)"),
    m: int = Query(..., ge=1, le=12, description="월 (1~12)"),
    d: int = Query(..., ge=1, le=31, description="일 (1~31)"),
    meal: Optional[str] = Query(None, description="특정 식사만 조회 (조식|중식|석식). 미지정 시 전체 반환"),
):
    # 달력 유효성 검증
    try:
        _ = date(y, m, d)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date (y, m, d)")

    try:
        data = parse_page_a(y, m, d)
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Upstream HTTP error: {str(e)}") from e
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {str(e)}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse error: {str(e)}") from e

    if meal is not None:
        meal = meal.strip()
        if meal not in ("조식", "중식", "석식"):
            raise HTTPException(status_code=400, detail="meal must be one of: 조식, 중식, 석식")
        return {"date": f"{y:04d}-{m:02d}-{d:02d}", "meal": meal, "items": data.get(meal, [])}

    return {
        "date": f"{y:04d}-{m:02d}-{d:02d}",
        "meals": {"조식": data.get("조식", []), "중식": data.get("중식", []), "석식": data.get("석식", [])},
    }


# ---- meals-B (신규: 요일별 요청) ----
@app.get("/meals-b")
def get_meals_b(
    day: str = Query(..., description="요일 (mon|tue|wed|thu|fri|sat|sun)"),
):
    day = day.strip().lower()
    if day not in DAY_TO_DIV_ID:
        raise HTTPException(status_code=400, detail="day must be one of: mon, tue, wed, thu, fri, sat, sun")

    try:
        parsed_date, meals = parse_page_b(day)
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Upstream HTTP error: {str(e)}") from e
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {str(e)}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse error: {str(e)}") from e

    payload = {
        "source": "B",
        "day": day,
        "cafeteria": "교직원 식당",
        "date": parsed_date,  # h3에서 파싱되면 "YYYY-MM-DD", 실패하면 None
        "meals": {
            "아침": meals.get("아침", []),
            "점심": meals.get("점심", []),
            "저녁": meals.get("저녁", []),
        },
    }

    # 데이터가 전부 비어있으면 note 추가(휴무/방학/페이지 변경 등)
    if not any(payload["meals"].values()):
        payload["note"] = "No menu found (possibly holiday/weekend or page format changed)."

    return payload
