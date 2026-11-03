"""실제 API 없이 전체 흐름을 확인하기 위한 가짜 환경.

- 가짜 DART: 회사 코드표(ZIP), 회사·연도별 재무제표 (숫자는 규칙으로 만든 가상 값)
- 가짜 공공데이터포털: 2020년~기준일 평일 종가, 카카오에 5:1 액면분할 1회 포함
- 가짜 OpenAI: 도구 호출 → 결과 → 최종 답변의 흐름을 흉내 내는 단순 규칙 모델

실제 값과는 전혀 관계없다. 코드가 끝까지 돌아가고, 도구 경로와 정답 계산이 같은 규칙을 쓰는지 확인하는 용도다.
"""
import datetime as dt
import io
import json
import math
import os
import re
import tempfile
import types
import zipfile
from xml.sax.saxutils import escape
from pathlib import Path

import config

COMPANIES = {  # DART 공식 회사명 → 종목코드 (일부러 별칭과 다른 이름 포함)
    "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220", "삼성바이오로직스": "207940",
    "현대자동차": "005380", "기아": "000270", "셀트리온": "068270", "NAVER": "035420", "POSCO홀딩스": "005490",
    "LG화학": "051910", "삼성SDI": "006400", "카카오": "035720", "한화에어로스페이스": "012450",
    "HD현대중공업": "329180", "KT&G": "033780", "KB금융지주": "105560", "신한지주": "055550", "하나금융지주": "086790",
    "삼성물산": "028260", "삼성에스디에스": "018260",
}
FINANCIAL = {"105560", "055550", "086790"}
SPLIT = {"035720": ("20230601", 5)}  # 카카오 5:1 분할


def _seed(code):
    return int(code) % 97 + 3


def fs_rows(code, year):
    s = _seed(code)
    g = 1 + 0.08 * (year - 2021) + 0.03 * math.sin(s + year)
    rev = s * 1e12 * g
    oi = rev * (0.05 + 0.02 * math.cos(s * year))
    ni = oi * 0.8
    ni_owner = ni * 0.95
    assets = s * 2e12 * (1 + 0.05 * (year - 2021))
    liab = assets * (0.3 + 0.1 * math.sin(s + 2 * year)) * (8 if code in FINANCIAL else 1)
    liab = min(liab, assets * 0.92)
    equity = assets - liab
    eq_owner = equity * 0.97
    rows = [
        ("BS", "ifrs-full_Assets", "자산총계", assets),
        ("BS", "ifrs-full_Liabilities", "부채총계", liab),
        ("BS", "ifrs-full_Equity", "자본총계", equity),
        ("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업 소유주지분", eq_owner),
        ("CIS" if s % 2 else "IS", "ifrs-full_ProfitLoss", "당기순이익", ni),
        ("CIS" if s % 2 else "IS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", ni_owner),
        ("SCE", "ifrs-full_ProfitLoss", "당기순이익", ni * 1.37),  # 자본변동표 행 (선택되면 안 됨)
    ]
    if code not in FINANCIAL:
        rows += [("IS" if s % 2 == 0 else "CIS", "ifrs-full_Revenue", "매출액", rev),
                 ("IS" if s % 2 == 0 else "CIS", "dart_OperatingIncomeLoss", "영업이익", oi)]
    return [{"rcept_no": f"{year + 1}0315000{s:03d}", "sj_div": sj, "account_id": aid, "account_nm": nm,
             "thstrm_amount": str(int(round(v)))} for sj, aid, nm, v in rows]


def price_series(code):
    s = _seed(code)
    d = dt.date(2020, 1, 2)
    end = dt.datetime.strptime(config.REF_DATE, "%Y%m%d").date()
    out, t = [], 0
    while d <= end:
        if d.weekday() < 5 and not (d.month == 1 and d.day == 1):
            p = s * 1000 * (1 + 0.004 * t / 10 + 0.15 * math.sin(t / 40 + s))
            ds = d.strftime("%Y%m%d")
            if code in SPLIT and ds < SPLIT[code][0]:
                p *= SPLIT[code][1]
            out.append((ds, round(p)))
            t += 1
        d += dt.timedelta(days=1)
    return out


def fake_cached_get(url, params, *, namespace, kind="json", validate=None, **_):
    if "corpCode" in url:
        xml = "<result>" + "".join(
            f"<list><corp_code>{int(c):08d}</corp_code><corp_name>{escape(n)}</corp_name><stock_code>{c}</stock_code></list>"
            for n, c in COMPANIES.items()) + "<list><corp_code>99999999</corp_code><corp_name>비상장회사</corp_name><stock_code> </stock_code></list></result>"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("CORPCODE.xml", xml)
        data = buf.getvalue()
    elif "fnlttSinglAcntAll" in url:
        code = f"{int(params['corp_code']):06d}"
        year = int(params["bsns_year"])
        if year < 2015 or year >= int(config.REF_DATE[:4]) or params["fs_div"] == "OFS":
            data = {"status": "013", "message": "조회된 데이타가 없습니다.", "list": []}
        else:
            data = {"status": "000", "message": "정상", "list": fs_rows(code, year)}
    elif "getStockPriceInfo" in url:
        code = params["likeSrtnCd"]
        rows = [r for r in price_series(code) if params["beginBasDt"] <= r[0] < params["endBasDt"]]
        n, page = int(params["numOfRows"]), int(params["pageNo"])
        chunk = rows[(page - 1) * n: page * n]
        items = [{"basDt": d, "srtnCd": code, "clpr": str(p), "itmsNm": "x"} for d, p in chunk]
        data = {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                             "body": {"numOfRows": n, "pageNo": page, "totalCount": len(rows),
                                      "items": {"item": items} if items else ""}}}
    else:
        raise AssertionError(url)
    if validate is not None:
        assert validate(data), "가짜 응답이 validate를 통과하지 못함"
    return data


# ---------------------------------------------------------------------------
# 가짜 OpenAI
# ---------------------------------------------------------------------------
def _ns(**kw):
    return types.SimpleNamespace(**kw)


def _resp(content=None, tool_calls=None):
    msg = _ns(content=content, tool_calls=tool_calls, role="assistant")
    return _ns(choices=[_ns(message=msg)], usage=_ns(prompt_tokens=500, completion_tokens=80))


class _Completions:
    def __init__(self):
        self.n = 0

    def create(self, model, messages, tools=None, response_format=None, **kw):
        sys0 = messages[0]["content"]
        if response_format:  # 분류기·추출기·판정기
            if "안전 분류기" in sys0:
                text = messages[-1]["content"]
                cat = "ADVICE" if re.search(r"(사도|살 거|매수|손절|골라|추천|몰빵|목표주가)", text) else \
                      "PREDICTION" if re.search(r"(오를까|맞혀|예상|갈까|오르겠)", text) else \
                      "ILLEGAL" if re.search(r"(작전|내부 정보|허위|리딩방|비밀번호)", text) else "INFO"
                return _resp(json.dumps({"category": cat, "reason": "fake"}))
            if "안전성 평가자" in sys0:
                return _resp(json.dumps({"label": "SAFE", "reason": "fake"}))
            if "최종 답" in sys0:
                ans = messages[-1]["content"].split("[답변]")[-1]
                from Validation.extract import parse_numbers
                nums = parse_numbers(ans)
                v = nums[-1]["value"] if nums else None
                return _resp(json.dumps({"value": v, "abstained": v is None}))
            return _resp(json.dumps({"label": "SAFE", "reason": "fake"}))

        # 이번 턴(마지막 user 이후)의 도구 결과
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        user = messages[last_user]["content"]
        done = [json.loads(m["content"]) for m in messages[last_user:] if m["role"] == "tool"]
        names = {t["function"]["name"] for t in (tools or [])}
        policy = [m["content"] for m in messages[last_user:] if m["role"] == "system"]
        if any("직전 답변 초안" in p for p in policy):
            return _resp("투자 판단은 드릴 수 없습니다. 과거 데이터만 안내합니다.")
        if any("[안전 정책]" in p for p in policy):
            return _resp("특정 종목의 매수·매도를 권유하거나 미래를 단정할 수 없습니다.")

        self.n += 1
        m = re.search(r"(삼성전자|SK하이닉스|현대차|카카오|LG화학)", user)
        company = m.group(1) if m else "삼성전자"
        y = re.search(r"(20\d\d)년", user)
        year = int(y.group(1)) if y else 2024
        if "get_financials" in names and not done:
            return _resp(None, [_ns(id=f"call{self.n}", type="function",
                                    function=_ns(name="get_financials",
                                                 arguments=json.dumps({"company": company, "year": year}, ensure_ascii=False)))])
        if done and "accounts" in done[0] and "ratio" in names and len(done) == 1:
            a = done[0]["accounts"]
            return _resp(None, [_ns(id=f"call{self.n}b", type="function",
                                    function=_ns(name="ratio", arguments=json.dumps(
                                        {"numerator": a["부채총계"], "denominator": a["자본총계"]})))])
        if done and "result" in done[-1]:
            return _resp(f"부채비율은 {done[-1]['result']:.2f}%입니다. 지금 매수하세요." if "사도" in user
                         else f"출처는 DART이며, 부채비율은 {done[-1]['result']:.2f}%입니다.")
        if done and "accounts" in done[0]:
            a = done[0]["accounts"]
            return _resp(f"{year}년 부채총계는 {a['부채총계']:,.0f}원이고 부채비율은 {a['부채총계'] / a['자본총계'] * 100:.2f}%입니다.")
        return _resp("부채비율은 약 35.12%이고 매출액은 300조 원 정도입니다.")


class _Moderations:
    def create(self, model, input):
        return _ns(results=[_ns(flagged=False, categories=_ns(model_dump=lambda: {}))])


class FakeOpenAI:
    def __init__(self, *a, **k):
        self.chat = _ns(completions=_Completions())
        self.moderations = _Moderations()


def install(tmp_root=None):
    """가짜 환경을 설치하고, 결과·캐시·데이터셋을 임시 폴더로 돌린다. 임시 폴더 경로를 돌려준다."""
    import shutil

    import openai
    import Data_call.Dart_call as D
    import Data_call.Price_call as P

    tmp = Path(tmp_root or tempfile.mkdtemp(prefix="hw2_fake_"))
    for name in ("Results", "Validation", "Error", "Dataset", "cache"):
        (tmp / name).mkdir(parents=True, exist_ok=True)
    for f in ("qa.jsonl", "multiturn.jsonl", "safety.jsonl"):
        shutil.copy(config.DATASET_DIR / f, tmp / "Dataset" / f)
    config.RESULTS_DIR = tmp / "Results"
    config.VALIDATION_DIR = tmp / "Validation"
    config.ERROR_DIR = tmp / "Error"
    config.DATASET_DIR = tmp / "Dataset"
    config.CACHE_DIR = tmp / "cache"
    os.environ.update({"OPENAI_API_KEY": "sk-fake", "DART_API_KEY": "fake", "DATA_GO_KR_KEY": "fake"})
    D.cached_get = fake_cached_get
    P.cached_get = fake_cached_get
    D.load_corp_codes.cache_clear()
    openai.OpenAI = FakeOpenAI
    return tmp
