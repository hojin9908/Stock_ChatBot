"""Tool 1이 올바른 데이터를 돌려주기 위한 입력 정리.

ablation 대상이 아니며 Tool 1이 켜져 있으면 항상 함께 동작한다.
- 회사명 → DART 고유번호(corp_code)·종목코드(stock_code)
- 날짜 문자열 → YYYYMMDD, 휴장일 → 다음 거래일
- 사업보고서 공시 여부
- 액면분할 보정 (계획서 2.1절 식)
"""
import datetime as dt
import re

import config
from Data_call.Dart_call import load_corp_codes

# 사용자가 흔히 쓰는 이름 → 종목코드. DART 공식 회사명과 다른 이름(현대차 ↔ 현대자동차)을 흡수한다.
ALIASES = {
    "삼성전자": "005930", "삼전": "005930",
    "SK하이닉스": "000660", "하이닉스": "000660", "에스케이하이닉스": "000660",
    "LG에너지솔루션": "373220", "엘지에너지솔루션": "373220", "LG엔솔": "373220", "엘지엔솔": "373220",
    "삼성바이오로직스": "207940", "삼바": "207940",
    "현대차": "005380", "현대자동차": "005380",
    "기아": "000270", "기아차": "000270",
    "셀트리온": "068270",
    "NAVER": "035420", "네이버": "035420",
    "POSCO홀딩스": "005490", "포스코홀딩스": "005490",
    "LG화학": "051910", "엘지화학": "051910",
    "삼성SDI": "006400",
    "카카오": "035720",
    "한화에어로스페이스": "012450", "한화에어로": "012450",
    "HD현대중공업": "329180", "현대중공업": "329180",
    "KT&G": "033780",
    "KB금융": "105560", "KB금융지주": "105560",
    "신한지주": "055550", "신한금융지주": "055550", "신한금융": "055550",
    "하나금융지주": "086790", "하나금융": "086790",
}


def _norm_name(name: str) -> str:
    return re.sub(r"[\s()㈜]|주식회사", "", str(name)).upper()


_ALIAS_NORM = {_norm_name(k): v for k, v in ALIASES.items()}


def resolve_company(name: str) -> dict:
    """회사명을 코드로 바꾼다.

    반환
      {"status": "ok", "corp_name", "corp_code", "stock_code"}
      {"status": "ambiguous", "candidates": [회사명, ...]}  → 챗봇이 사용자에게 되묻는다
      {"status": "not_found"}
    """
    corps = load_corp_codes()
    by_stock = {c["stock_code"]: c for c in corps}
    key = _norm_name(name)
    if not key:
        return {"status": "not_found"}

    stock = _ALIAS_NORM.get(key)
    if stock is None and re.fullmatch(r"\d{6}", key):
        stock = key  # 종목코드를 직접 넣은 경우
    if stock and stock in by_stock:
        return {"status": "ok", **by_stock[stock]}

    exact = [c for c in corps if _norm_name(c["corp_name"]) == key]
    if len(exact) == 1:
        return {"status": "ok", **exact[0]}

    partial = [c for c in corps if key in _norm_name(c["corp_name"])]
    if len(partial) == 1:
        return {"status": "ok", **partial[0]}
    if partial:
        partial.sort(key=lambda c: len(c["corp_name"]))
        return {"status": "ambiguous", "candidates": [c["corp_name"] for c in partial[:10]]}
    return {"status": "not_found"}


def parse_date(s: str) -> str:
    """'2025-01-02', '2025.1.2', '20250102', '2025/01/02' → '20250102'."""
    s = str(s).strip()
    if re.fullmatch(r"\d{8}", s):
        dt.datetime.strptime(s, "%Y%m%d")
        return s
    m = re.fullmatch(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?", s)
    if not m:
        raise ValueError(f"날짜 형식을 해석할 수 없습니다: {s} (YYYY-MM-DD로 주세요)")
    out = f"{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    dt.datetime.strptime(out, "%Y%m%d")
    return out


def fmt_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def is_future(yyyymmdd: str) -> bool:
    return yyyymmdd > config.REF_DATE


def is_annual_report_published(year: int) -> bool:
    """사업보고서는 사업연도 종료 후 90일 이내(3월 말)에 공시된다."""
    return f"{year + 1}0331" <= config.REF_DATE


def adjust_splits(prices: list) -> tuple:
    """액면분할을 찾아 그 이전 가격을 분할 비율로 나눈다.

    전일 대비 종가 비 r = P(t-1)/P(t)가 정수 k(≥2)에 5% 이내로 가까우면 k:1 분할로 본다.
    국내 하루 하락폭은 최대 30%라서 r은 최대 1.43이므로 실제 급락과 혼동되지 않는다.

    반환: ([(날짜, 보정가), ...], [(분할일, k), ...])
    """
    if not prices:
        return [], []
    factor_after = [1.0] * len(prices)  # 각 날짜 이후에 일어난 분할 비율의 곱
    splits = []
    cum = 1.0
    for i in range(len(prices) - 1, 0, -1):
        prev_close, close = prices[i - 1][1], prices[i][1]
        factor_after[i] = cum
        if close > 0:
            r = prev_close / close
            k = round(r)
            if k >= 2 and abs(r - k) / k < 0.05:
                splits.append((prices[i][0], k))
                cum *= k
    factor_after[0] = cum
    adjusted = [(d, p / f) for (d, p), f in zip(prices, factor_after)]
    return adjusted, sorted(splits)


def first_on_or_after(prices: list, yyyymmdd: str):
    """yyyymmdd 당일 또는 그 이후 첫 거래일의 (날짜, 가격). 없으면 None."""
    for d, p in prices:
        if d >= yyyymmdd:
            return d, p
    return None


def last_on_or_before(prices: list, yyyymmdd: str):
    """yyyymmdd 당일 또는 그 이전 마지막 거래일의 (날짜, 가격). 없으면 None."""
    found = None
    for d, p in prices:
        if d <= yyyymmdd:
            found = (d, p)
        else:
            break
    return found


def monthly_first_trading_days(prices: list, start: str, end: str) -> list:
    """start 이후(포함) 도래하는 매월 첫 거래일들. 그달 첫 거래일이 start보다 앞이면 그달은 건너뛴다."""
    seen, out = set(), []
    for d, p in prices:
        month = d[:6]
        if month in seen:
            continue
        seen.add(month)  # 이 날짜가 그달의 첫 거래일이다
        if start <= d <= end:
            out.append((d, p))
    return out
