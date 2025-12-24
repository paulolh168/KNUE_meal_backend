from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from lxml import html


BASE_URL = "http://rec.knue.ac.kr/pub/admi/admi050701.jsp"

# XPath와 식사명 매핑
MEAL_XPATHS = [
    ("조식", '//*[@id="contents"]/div/div[2]/table/tbody/tr[1]/td'),
    ("중식", '//*[@id="contents"]/div/div[2]/table/tbody/tr[2]/td'),
    ("석식", '//*[@id="contents"]/div/div[2]/table/tbody/tr[3]/td'),
]


def build_url(y: int, m: int, d: int) -> str:
    return f"{BASE_URL}?year={y}&month={m}&date={d}"


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()

    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return r.content.decode(enc)
        except UnicodeDecodeError:
            continue

    return r.content.decode("utf-8", errors="replace")


def xpath_without_tbody(xpath: str) -> str:
    return xpath.replace("/tbody", "")


def extract_td_lines_preserve_br(td) -> List[str]:
    """
    <br> 기준 줄바꿈 보존 + tail 텍스트 포함 (손실 방지)
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


def extract_by_xpath(tree, xpath: str):
    nodes = tree.xpath(xpath)
    if not nodes:
        alt = xpath_without_tbody(xpath)
        nodes = tree.xpath(alt)
    return nodes


def parse_page(y: int, m: int, d: int) -> Dict[str, List[str]]:
    url = build_url(y, m, d)
    html_text = fetch_html(url)
    tree = html.fromstring(html_text)

    result: Dict[str, List[str]] = {}

    for meal_name, xp in MEAL_XPATHS:
        tds = extract_by_xpath(tree, xp)

        meal_items: List[str] = []
        for td in tds:
            meal_items.extend(extract_td_lines_preserve_br(td))

        result[meal_name] = meal_items

    return result


app = FastAPI(
    title="KNUE Meal API",
    version="1.0.0",
    description="한국교원대학교 사도교육원 식단 페이지를 크롤링하여 JSON API로 제공합니다.",
)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/meals")
def get_meals(
    y: int = Query(..., ge=2000, le=2100, description="연도 (예: 2025)"),
    m: int = Query(..., ge=1, le=12, description="월 (1~12)"),
    d: int = Query(..., ge=1, le=31, description="일 (1~31)"),
    meal: Optional[str] = Query(
        None,
        description="특정 식사만 조회 (조식|중식|석식). 미지정 시 전체 반환",
    ),
):
    try:
        data = parse_page(y, m, d)
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Upstream HTTP error: {str(e)}") from e
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {str(e)}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse error: {str(e)}") from e

    if meal is not None:
        if meal not in ("조식", "중식", "석식"):
            raise HTTPException(status_code=400, detail="meal must be one of: 조식, 중식, 석식")
        return {
            "date": f"{y:04d}-{m:02d}-{d:02d}",
            "meal": meal,
            "items": data.get(meal, []),
        }

    return {
        "date": f"{y:04d}-{m:02d}-{d:02d}",
        "meals": {
            "조식": data.get("조식", []),
            "중식": data.get("중식", []),
            "석식": data.get("석식", []),
        },
    }
