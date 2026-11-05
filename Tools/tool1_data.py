"""Tool 1: 재무제표와 주가의 원값을 돌려주는 도구.

규칙: 비율·증감률·수익률은 절대 계산하지 않는다. 원값만 돌려준다.
그래야 "Tool 1만 켜진 구성(C1)"에서 계산을 LLM이 하게 되어 Tool 2의 효과를 따로 잴 수 있다.

모든 함수는 예외를 던지지 않고 {"error": "..."}를 돌려준다. 챗봇이 그 내용을 보고 사용자에게 설명한다.
"""
from functools import lru_cache

import config
from Data_call.Dart_call import DartError, fetch_financial_statement
from Data_call.Price_call import fetch_daily_prices
from Tools import normalize as nz

# (표시 이름, DART 표준계정 ID 후보, 계정명 후보, 우선 재무제표 구분)
# sj_div: BS 재무상태표, IS 손익계산서, CIS 포괄손익계산서
ACCOUNT_SPEC = [
    ("매출액", ["ifrs-full_Revenue"], ["매출액", "수익(매출액)", "영업수익", "매출"], ["IS", "CIS"]),
    ("영업이익", ["dart_OperatingIncomeLoss"], ["영업이익", "영업이익(손실)"], ["IS", "CIS"]),
    ("당기순이익", ["ifrs-full_ProfitLoss"], ["당기순이익", "당기순이익(손실)", "연결당기순이익"], ["IS", "CIS"]),
    ("지배주주순이익", ["ifrs-full_ProfitLossAttributableToOwnersOfParent"],
     ["지배기업의 소유주에게 귀속되는 당기순이익", "지배기업 소유주지분", "지배기업소유주지분"], ["IS", "CIS"]),
    ("자산총계", ["ifrs-full_Assets"], ["자산총계"], ["BS"]),
    ("부채총계", ["ifrs-full_Liabilities"], ["부채총계"], ["BS"]),
    ("자본총계", ["ifrs-full_Equity"], ["자본총계"], ["BS"]),
    ("지배주주자본", ["ifrs-full_EquityAttributableToOwnersOfParent"],
     ["지배기업의 소유주에게 귀속되는 자본", "지배기업 소유주지분", "지배기업소유주지분"], ["BS"]),
]
FS_NAME = {"CFS": "연결", "OFS": "별도"}


def _to_number(s):
    s = str(s or "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_accounts(rows: list) -> dict:
    """DART 재무제표 행 목록에서 ACCOUNT_SPEC의 계정 값을 뽑는다. 못 찾으면 None."""
    out = {}
    for label, ids, names, sj_pref in ACCOUNT_SPEC:
        value = None
        for sj in sj_pref:
            cand = [r for r in rows if r.get("sj_div") == sj and r.get("account_id") in ids]
            if not cand:
                cand = [r for r in rows if r.get("sj_div") == sj
                        and str(r.get("account_nm", "")).replace(" ", "") in [n.replace(" ", "") for n in names]]
            for r in cand:
                value = _to_number(r.get("thstrm_amount"))
                if value is not None:
                    break
            if value is not None:
                break
        out[label] = value
    # 비지배지분이 없는 회사는 지배/비지배 구분 계정을 공시하지 않는다. 이때 지배주주 몫 = 전체다.
    if not _has_noncontrolling(rows):
        for owner, total in OWNER_FALLBACK.items():
            if out.get(owner) is None and out.get(total) is not None:
                out[owner] = out[total]
    return out


OWNER_FALLBACK = {"지배주주순이익": "당기순이익", "지배주주자본": "자본총계"}


def _has_noncontrolling(rows: list) -> bool:
    """재무제표에 비지배지분 계정이 하나라도 있으면 True."""
    return any("NonControllingInterest" in str(r.get("account_id", "")) or "비지배" in str(r.get("account_nm", ""))
               for r in rows)


def _resolve(company: str):
    r = nz.resolve_company(company)
    if r["status"] == "ok":
        return r, None
    if r["status"] == "ambiguous":
        return None, {"error": "회사명이 모호합니다. 사용자에게 어느 회사인지 물어보세요.", "candidates": r["candidates"]}
    return None, {"error": f"'{company}'에 해당하는 상장사를 찾지 못했습니다."}


def get_financials(company: str, year: int) -> dict:
    """회사·사업연도의 주요 계정 원값 (연간 사업보고서, 연결 우선, 없으면 별도)."""
    corp, err = _resolve(company)
    if err:
        return err
    try:
        year = int(year)
    except (TypeError, ValueError):
        return {"error": f"연도를 해석할 수 없습니다: {year}"}
    if not nz.is_annual_report_published(year):
        return {"error": f"{year}년 사업보고서는 기준일({nz.fmt_date(config.REF_DATE)}) 현재 아직 공시되지 않았습니다."}
    try:
        for fs_div in ("CFS", "OFS"):
            data = fetch_financial_statement(corp["corp_code"], year, "annual", fs_div)
            if data.get("status") == "000" and data["list"]:
                break
        else:
            return {"error": f"{corp['corp_name']} {year}년 재무제표가 DART에 없습니다."}
    except DartError as e:
        return {"error": f"DART 조회 실패: {e}"}
    rows = data["list"]
    return {
        "company": corp["corp_name"],
        "stock_code": corp["stock_code"],
        "year": year,
        "report": "사업보고서(연간)",
        "fs": FS_NAME[fs_div],
        "unit": "원",
        "accounts": extract_accounts(rows),
        "source": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rows[0].get('rcept_no', '')}",
    }


def get_financials_range(company: str, start_year: int, end_year: int) -> dict:
    """여러 사업연도의 주요 계정 원값을 연도별로 돌려준다 (최대 10년)."""
    try:
        start_year, end_year = int(start_year), int(end_year)
    except (TypeError, ValueError):
        return {"error": "연도는 정수로 주세요."}
    if end_year < start_year:
        start_year, end_year = end_year, start_year
    if end_year - start_year > 9:
        return {"error": "한 번에 최대 10개 연도까지 조회할 수 있습니다."}
    corp, err = _resolve(company)
    if err:
        return err
    years = []
    for y in range(start_year, end_year + 1):
        r = get_financials(company, y)
        years.append({"year": y, **({"error": r["error"]} if "error" in r else
                                    {"fs": r["fs"], "accounts": r["accounts"], "source": r["source"]})})
    return {"company": corp["corp_name"], "stock_code": corp["stock_code"], "unit": "원", "years": years}


@lru_cache(maxsize=64)
def _price_history(stock_code: str, begin: str) -> tuple:
    """begin ~ 기준일 원 종가와 분할 보정 종가. 보정은 기준일 가격 기준이다."""
    raw = fetch_daily_prices(stock_code, begin, config.REF_DATE)
    adjusted, splits = nz.adjust_splits(raw)
    return tuple(raw), tuple(adjusted), tuple(splits)


def get_price(company: str, date: str) -> dict:
    """date 당일(휴장일이면 다음 거래일) 종가. 원 종가와 액면분할 보정 종가를 함께 준다."""
    corp, err = _resolve(company)
    if err:
        return err
    try:
        d = nz.parse_date(date)
    except ValueError as e:
        return {"error": str(e)}
    if nz.is_future(d):
        return {"error": f"{nz.fmt_date(d)}은 기준일({nz.fmt_date(config.REF_DATE)}) 이후라 주가가 없습니다."}
    try:
        raw, adj, splits = _price_history(corp["stock_code"], d)
    except Exception as e:  # 네트워크·키 오류
        return {"error": f"주가 조회 실패: {e}"}
    hit = nz.first_on_or_after(list(raw), d)
    if hit is None:
        return {"error": f"{corp['corp_name']}의 {nz.fmt_date(d)} 이후 시세가 없습니다."}
    idx = [x[0] for x in raw].index(hit[0])
    out = {
        "company": corp["corp_name"],
        "stock_code": corp["stock_code"],
        "requested_date": nz.fmt_date(d),
        "trading_date": nz.fmt_date(hit[0]),
        "close": hit[1],
        "adj_close": round(adj[idx][1], 4),
        "unit": "원",
        "source": "공공데이터포털 금융위원회_주식시세정보",
    }
    if splits:
        out["note"] = "기간 중 액면분할이 있어 수익률 계산에는 adj_close(수정주가)를 써야 합니다."
        out["splits"] = [{"date": nz.fmt_date(s), "ratio": k} for s, k in splits]
    return out


def get_price_series(company: str, start_date: str, end_date: str = "") -> dict:
    """start_date가 속한 달부터 매월 첫 거래일의 수정 종가 목록과 end_date(기본: 기준일)의 수정 종가.

    적립식 투자 계산에 쓴다. 계산은 하지 않는다.
    """
    corp, err = _resolve(company)
    if err:
        return err
    try:
        s = nz.parse_date(start_date)
        e = nz.parse_date(end_date) if end_date else config.REF_DATE
    except ValueError as ex:
        return {"error": str(ex)}
    if nz.is_future(s):
        return {"error": f"시작일 {nz.fmt_date(s)}이 기준일 이후입니다."}
    e = min(e, config.REF_DATE)
    try:
        # 그달 1일부터 받아야 "그달 첫 거래일"을 정확히 알 수 있다
        raw, adj, splits = _price_history(corp["stock_code"], s[:6] + "01")
    except Exception as ex:
        return {"error": f"주가 조회 실패: {ex}"}
    adj = list(adj)
    monthly = nz.monthly_first_trading_days(adj, s, e)
    last = nz.last_on_or_before(adj, e)
    if not monthly or last is None:
        return {"error": f"{corp['corp_name']}의 해당 기간 시세가 없습니다."}
    out = {
        "company": corp["corp_name"],
        "stock_code": corp["stock_code"],
        "rule": "시작일이 속한 달부터 매월 첫 거래일의 수정 종가",
        "monthly_first_trading_day_adj_close": [{"date": nz.fmt_date(d), "adj_close": round(p, 4)} for d, p in monthly],
        "end": {"date": nz.fmt_date(last[0]), "adj_close": round(last[1], 4)},
        "unit": "원",
        "source": "공공데이터포털 금융위원회_주식시세정보",
    }
    if splits:
        out["splits"] = [{"date": nz.fmt_date(d), "ratio": k} for d, k in splits]
    return out
