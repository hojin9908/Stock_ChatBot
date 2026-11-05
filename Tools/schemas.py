"""각 도구 함수의 설명서(OpenAI function calling JSON 형식)를 정의하고,
켜진 도구의 설명서만 모아 챗봇에 넘긴다.

꺼진 도구는 설명서 목록에서 아예 빠진다. 모델이 그 도구의 존재를 모르게 해야 진짜 "off"다.
"""
from Tools import tool1_data, tool2_calc


def _fn(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOL1_SPECS = [
    _fn("get_financials",
        "DART 공시(연간 사업보고서, 연결 우선)에서 한 회사·한 사업연도의 주요 계정 원값을 조회한다. "
        "매출액, 영업이익, 당기순이익, 지배주주순이익, 자산총계, 부채총계, 자본총계, 지배주주자본(원 단위)을 돌려준다. "
        "비율은 계산해 주지 않는다.",
        {"company": {"type": "string", "description": "회사명. 예: 삼성전자, 현대차"},
         "year": {"type": "integer", "description": "사업연도. 예: 2024"}},
        ["company", "year"]),
    _fn("get_financials_range",
        "여러 사업연도의 주요 계정 원값을 연도별로 한 번에 조회한다 (최대 10년).",
        {"company": {"type": "string"},
         "start_year": {"type": "integer"},
         "end_year": {"type": "integer"}},
        ["company", "start_year", "end_year"]),
    _fn("get_price",
        "특정 날짜의 종가를 조회한다. 휴장일이면 다음 거래일 종가를 준다. "
        "close는 실제 종가, adj_close는 액면분할을 반영한 수정 종가다. 수익률 계산에는 adj_close를 쓴다.",
        {"company": {"type": "string"},
         "date": {"type": "string", "description": "YYYY-MM-DD"}},
        ["company", "date"]),
    _fn("get_price_series",
        "적립식 투자 계산용. start_date 이후 도래하는 매월 첫 거래일의 수정 종가 목록과 "
        "end_date(생략 시 오늘)의 수정 종가를 조회한다.",
        {"company": {"type": "string"},
         "start_date": {"type": "string", "description": "YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "YYYY-MM-DD, 생략하면 오늘"}},
        ["company", "start_date"]),
]

TOOL2_SPECS = [
    _fn("ratio",
        "numerator ÷ denominator를 계산한다. as_percent=true면 ×100 한 %를 돌려준다. "
        "부채비율(부채총계/자본총계), ROE(지배주주순이익/지배주주자본), 영업이익률(영업이익/매출액)에 쓴다.",
        {"numerator": {"type": "number"}, "denominator": {"type": "number"},
         "as_percent": {"type": "boolean", "description": "기본 true"}},
        ["numerator", "denominator"]),
    _fn("growth",
        "증감률(%) = (new_value − old_value) ÷ |old_value| × 100.",
        {"old_value": {"type": "number"}, "new_value": {"type": "number"}},
        ["old_value", "new_value"]),
    _fn("cagr",
        "연평균 성장률(%) = (end_value ÷ start_value)^(1/years) − 1. years는 시작 연도와 끝 연도의 차이.",
        {"start_value": {"type": "number"}, "end_value": {"type": "number"}, "years": {"type": "number"}},
        ["start_value", "end_value", "years"]),
    _fn("select_year",
        "연도별 값 표에서 값이 가장 작은(min) 또는 큰(max) 연도를 고른다.",
        {"table": {"type": "array", "items": {"type": "object", "properties": {
            "year": {"type": "integer"}, "value": {"type": "number"}}, "required": ["year", "value"]}},
         "rule": {"type": "string", "enum": ["min", "max"]}},
        ["table", "rule"]),
    _fn("simulate",
        "같은 금액을 한 번(거치식) 또는 여러 번(적립식) 매수했을 때 투자원금, 평가액, 수익률을 계산한다. "
        "소수점 주식 허용, 수수료·세금·배당 미반영.",
        {"buy_prices": {"type": "array", "items": {"type": "number"},
                        "description": "매수 시점 가격들. 거치식이면 1개"},
         "amount_per_buy": {"type": "number", "description": "1회 매수 금액(원)"},
         "valuation_price": {"type": "number", "description": "평가 시점 가격"}},
        ["buy_prices", "amount_per_buy", "valuation_price"]),
    _fn("calculator",
        "그 밖의 사칙연산·거듭제곱 계산. 예: '(1500/1000)**(1/3)-1'",
        {"expression": {"type": "string"}},
        ["expression"]),
]

TOOL1_FUNCS = {
    "get_financials": tool1_data.get_financials,
    "get_financials_range": tool1_data.get_financials_range,
    "get_price": tool1_data.get_price,
    "get_price_series": tool1_data.get_price_series,
}
TOOL2_FUNCS = {
    "ratio": tool2_calc.ratio,
    "growth": tool2_calc.growth,
    "cagr": tool2_calc.cagr,
    "select_year": tool2_calc.select_year,
    "simulate": tool2_calc.simulate,
    "calculator": tool2_calc.calculator,
}


def build_tools(tool1: bool, tool2: bool) -> tuple:
    """켜진 도구의 (설명서 리스트, {함수 이름: 파이썬 함수})를 돌려준다. 둘 다 꺼지면 ([], {})."""
    specs, funcs = [], {}
    if tool1:
        specs += TOOL1_SPECS
        funcs.update(TOOL1_FUNCS)
    if tool2:
        specs += TOOL2_SPECS
        funcs.update(TOOL2_FUNCS)
    return specs, funcs
