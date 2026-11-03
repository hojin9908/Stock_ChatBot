"""틀린 문항마다 원인을 자동으로 판정한다 (계획서 6.2절).

파이프라인 순서대로 검사해 "처음 어긋난 단계" 하나만 원인으로 기록한다.
앞 단계의 오류가 뒤 단계 오류를 유발하므로 처음 것만 센다.

  E1 도구 선택 오류   : 필요한 종류의 도구를 부르지 않았다
  E2 인자 추출 오류   : 회사·연도·날짜·금액이 정답 인자와 다르다
  E3 개체 해석 오류   : 회사명은 맞게 넣었는데 다른 종목으로 해석됐다
  E4 데이터 수집 오류 : 도구가 오류를 돌려줬다 (API 오류, 한도 초과 등)
  E5 데이터 정규화 오류: 인자는 맞는데 도구가 돌려준 원값이 정답 원값과 다르다
  E6 생성·계산 오류   : 도구 원값은 맞는데 최종 답이 틀렸다 (전사 실수, LLM 계산 실수)
  E7 멀티턴 문맥 오류 : 2턴 이후에 이전 조건을 잃어 인자가 틀렸다
  E8 부당한 거절      : 답할 수 있는 질문에 답하지 않았다
  E9 채점 오류        : 추출기가 값을 잘못 읽었다 (사람 검토로만 판정)
  N1 기억 기반 오답   : 데이터 도구가 없는 구성에서 틀린 값을 말했다
  F1 근거 없는 답변   : 답할 수 없는 질문(F 유형)에 숫자를 제시했다

자동 판정은 휴리스틱이다. error_cases.csv의 labeler1, labeler2 칸으로 사람이 확인한다.
"""
import json
import re

import config

FIN_TOOLS = {"get_financials", "get_financials_range"}
PRICE_TOOLS = {"get_price", "get_price_series"}


def _norm(s):
    return re.sub(r"\s", "", str(s or "")).upper()


def _args(call):
    try:
        return json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return {}


def _date8(s):
    digits = re.sub(r"\D", "", str(s or ""))
    return digits[:8] if len(digits) >= 8 else ""


def required_years(ga: dict) -> set:
    k = ga["kind"]
    if k == "account":
        return {ga["year"]}
    if k == "metric":
        if ga["metric"] == "증가율":
            return {ga["year"] - 1, ga["year"]}
        if ga["metric"] == "CAGR":
            return {ga["start_year"], ga["end_year"]}
        return {ga["year"]}
    if k == "select_invest":
        return set(range(ga["start_year"], ga["end_year"] + 1))
    return set()


def needed_tools(ga: dict) -> list:
    """필요한 도구 종류 목록 (각 원소는 '이 중 하나'라는 뜻의 집합)."""
    k = ga["kind"]
    if k in ("account", "metric"):
        return [FIN_TOOLS]
    if k == "price":
        return [PRICE_TOOLS]
    if k == "invest":
        return [{"get_price_series"}] if ga["mode"] == "monthly" else [PRICE_TOOLS]
    if k == "select_invest":
        return [FIN_TOOLS, PRICE_TOOLS]
    return []


def attribute(cfg: str, gold: dict, score: dict, trace: dict, turn_index: int = 0) -> tuple:
    """(원인 코드, 설명). 정답이면 (None, "")."""
    status = gold.get("gold_status")
    if status == "abstain":
        return (None, "") if score["abstained"] else ("F1", "답할 수 없는 질문에 숫자를 제시")
    if status != "ok" or score.get("correct1"):
        return None, ""
    if score["abstained"]:
        return "E8", "답할 수 있는 질문에 답하지 않음"

    tool1 = config.CONFIGS.get(cfg, (False,))[0]
    if not tool1:
        return "N1", "데이터 도구 없이 기억으로 답한 값이 틀림"

    ga = gold["gold_args"]
    calls = trace.get("tool_calls", [])
    names = {c["name"] for c in calls}
    for group in needed_tools(ga):
        if not names & group:
            return "E1", f"필요한 도구({'/'.join(sorted(group))})를 호출하지 않음"

    gold_code = ga.get("stock_code")
    gold_name = _norm(ga.get("company"))
    company_calls = [c for c in calls if c["name"] in FIN_TOOLS | PRICE_TOOLS]

    # 회사
    good_company = [c for c in company_calls if (c.get("result") or {}).get("stock_code") == gold_code]
    if not good_company:
        wrong_name = any(_norm(_args(c).get("company")) != gold_name for c in company_calls)
        errored = [c for c in company_calls if "error" in (c.get("result") or {})]
        if errored and not wrong_name:
            return "E4", f"도구 오류: {errored[0]['result']['error'][:80]}"
        if wrong_name:
            code = "E7" if turn_index > 0 else "E2"
            return code, f"회사 인자가 다름: {[_args(c).get('company') for c in company_calls][:3]}"
        return "E3", "회사명은 같으나 다른 종목으로 해석됨"

    # 연도
    years = required_years(ga)
    if years:
        called = set()
        for c in calls:
            a = _args(c)
            if c["name"] == "get_financials" and str(a.get("year", "")).isdigit():
                called.add(int(a["year"]))
            if c["name"] == "get_financials_range":
                try:
                    called |= set(range(int(a["start_year"]), int(a["end_year"]) + 1))
                except (KeyError, ValueError, TypeError):
                    pass
        missing = years - called
        if missing:
            code = "E7" if turn_index > 0 else "E2"
            return code, f"필요한 연도 {sorted(missing)}를 조회하지 않음"

    # 날짜·금액
    detail = gold.get("gold_detail") or {}
    if ga["kind"] in ("invest", "select_invest", "price"):
        want = (detail.get("buy_dates") or [None])[0] if ga["kind"] != "price" else detail.get("trading_date")
        got = []
        for c in calls:
            res = c.get("result") or {}
            if c["name"] == "get_price" and "trading_date" in res:
                got.append(_date8(res["trading_date"]))
            if c["name"] == "get_price_series" and res.get("monthly_first_trading_day_adj_close"):
                got.append(_date8(res["monthly_first_trading_day_adj_close"][0]["date"]))
        if want and want not in got:
            errored = [c for c in calls if c["name"] in PRICE_TOOLS and "error" in (c.get("result") or {})]
            if errored and not got:
                return "E4", f"주가 도구 오류: {errored[0]['result']['error'][:80]}"
            code = "E7" if turn_index > 0 else "E2"
            return code, f"매수일이 다름: 정답 {want}, 조회 {got[:3]}"
        if "amount" in ga:
            sims = [c for c in calls if c["name"] == "simulate"]
            if sims and all(abs(float(_args(c).get("amount_per_buy", 0) or 0) - ga["amount"]) > 1 for c in sims):
                code = "E7" if turn_index > 0 else "E2"
                return code, f"투자 금액 인자가 다름: {[_args(c).get('amount_per_buy') for c in sims]}"

    # 원값
    if ga["kind"] == "account":
        values = []
        for c in calls:
            res = c.get("result") or {}
            if res.get("year") == ga["year"]:
                values.append((res.get("accounts") or {}).get(ga["account"]))
            for y in res.get("years", []):
                if y.get("year") == ga["year"]:
                    values.append((y.get("accounts") or {}).get(ga["account"]))
        values = [v for v in values if v is not None]
        if not values:
            return "E5", f"도구 결과에 {ga['year']}년 {ga['account']} 계정이 없음"
        g = gold.get("gold_value")
        if g and all(abs(v - g) / abs(g) > 1e-3 for v in values):
            return "E5", f"도구 원값 {values[0]} ≠ 정답 원값 {g}"

    return "E6", "도구 원값은 맞으나 최종 답이 틀림 (전사·계산·단위)"
