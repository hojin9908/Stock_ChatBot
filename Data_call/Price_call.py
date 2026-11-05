"""공공데이터포털 '금융위원회_주식시세정보' API에서 일별 종가 원본을 받아온다.

이 파일은 액면분할 보정이나 거래일 맞추기 같은 가공을 하지 않는다 (Tools/normalize.py가 한다).

요청 단위는 "종목 × 1년"이다. 연도 단위로 나눠 받아 캐시하면,
기간이 조금씩 다른 질문이 와도 같은 캐시 파일을 재사용할 수 있다.
"""
import datetime as dt

import config
from API_key.API import get_key
from Data_call.cache import cached_get

PRICE_URL = "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
ROWS_PER_PAGE = 1000


def _next_day(yyyymmdd: str) -> str:
    return (dt.datetime.strptime(yyyymmdd, "%Y%m%d") + dt.timedelta(days=1)).strftime("%Y%m%d")


def _is_ok(d) -> bool:
    try:
        return d["response"]["header"]["resultCode"] == "00"
    except (KeyError, TypeError):
        return False


def fetch_year(stock_code: str, year: int) -> list:
    """한 종목의 한 해 일별 종가. 평가 기준일(REF_DATE) 이후 데이터는 받지 않는다.

    반환: [("20250102", 53400.0), ...] 날짜 오름차순
    """
    begin = f"{year}0101"
    end_inclusive = min(f"{year}1231", config.REF_DATE)
    if begin > end_inclusive:
        return []
    out, page = [], 1
    while True:
        data = cached_get(
            PRICE_URL,
            {
                "serviceKey": get_key("data_go_kr"),
                "resultType": "json",
                "numOfRows": ROWS_PER_PAGE,
                "pageNo": page,
                "likeSrtnCd": stock_code,
                "beginBasDt": begin,
                "endBasDt": _next_day(end_inclusive),  # endBasDt는 "미만" 조건이다
            },
            namespace="price",
            validate=_is_ok,
        )
        body = data["response"]["body"]
        items = body.get("items") or {}
        items = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(items, dict):  # 결과가 1건이면 리스트가 아니라 dict로 온다
            items = [items]
        for it in items:
            # likeSrtnCd는 "포함" 검색이므로 정확히 같은 종목만 남긴다
            if str(it.get("srtnCd", "")).strip().lstrip("A") != stock_code:
                continue
            out.append((str(it["basDt"]), float(it["clpr"])))
        total = int(body.get("totalCount", 0) or 0)
        if page * ROWS_PER_PAGE >= total:
            break
        page += 1
    return sorted(set(out))


def fetch_daily_prices(stock_code: str, begin: str, end: str) -> list:
    """begin~end(둘 다 포함, YYYYMMDD) 일별 종가. 연도별 요청을 합쳐서 돌려준다."""
    end = min(end, config.REF_DATE)
    if begin > end:
        return []
    out = []
    for year in range(int(begin[:4]), int(end[:4]) + 1):
        out.extend(p for p in fetch_year(stock_code, year) if begin <= p[0] <= end)
    return sorted(set(out))
