"""챗봇의 자유 문장 답변에서 숫자를 뽑는다.

1. parse_numbers(text): 답변 속 모든 금액·비율 숫자 (규칙 기반). 수치 환각률 계산에 쓴다.
   "1조 2,345억 원" → 1_234_500_000_000 원, "14.47%" → 14.47 %.
2. extract_final(question, answer, unit): 질문에 대한 "최종 답" 하나 (보조 LLM, 결과는 캐시).
   모든 구성에 같은 추출기를 쓰므로 비교가 공정하다. 정확도는 extractor_check.csv로 사람이 확인한다.
"""
import hashlib
import json
import re

import config

UNIT_MULT = {"조": 1e12, "억": 1e8, "만": 1e4, "천": 1e3}
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_TOKEN = re.compile(rf"({_NUM})\s*(조|억|만|천)?")
_URL = re.compile(r"https?://\S+")
_DATES = [
    re.compile(r"\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}"),
    re.compile(r"\d{4}\s*년\s*\d{1,2}\s*월(\s*\d{1,2}\s*일)?"),
    re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일"),
    re.compile(r"(?<!\d)20\d{6}(?!\d)"),
]
# 숫자 바로 뒤에 오면 금액·비율이 아닌 것
_NOT_VALUE_AFTER = re.compile(r"^\s*(년|월|일|분기|개월|회|번|차|위|개|명|세|주\b|주차|종목|건|단계|턴|시|배)")


def parse_numbers(text: str) -> list:
    """반환: [{"value": float, "unit": "원"|"%"|"", "raw": str}, ...]"""
    text = _URL.sub(" ", text or "")
    for pat in _DATES:
        text = pat.sub(lambda m: " " * len(m.group(0)), text)
    out = []
    matches = list(_TOKEN.finditer(text))
    i = 0
    while i < len(matches):
        m = matches[i]
        num_s, unit = m.group(1), m.group(2)
        if num_s.startswith("0") and len(num_s) == 6:  # 종목코드(005930)
            i += 1
            continue
        value = float(num_s.replace(",", "")) * UNIT_MULT.get(unit, 1)
        start, end = m.start(), m.end()
        last_mult = UNIT_MULT.get(unit, 0)
        # "1조 2,345억 6,789만" 처럼 큰 단위부터 이어지는 덩어리를 합친다
        while unit and i + 1 < len(matches):
            nxt = matches[i + 1]
            gap = text[end:nxt.start()]
            nunit = nxt.group(2)
            if gap.strip() == "" and nunit and UNIT_MULT[nunit] < last_mult:
                value += float(nxt.group(1).replace(",", "")) * UNIT_MULT[nunit]
                last_mult = UNIT_MULT[nunit]
                end = nxt.end()
                unit = nunit
                i += 1
            else:
                break
        tail = text[end:end + 6]
        if _NOT_VALUE_AFTER.match(tail):
            i += 1
            continue
        if re.match(r"^\s*(%|퍼센트|％)", tail):
            u = "%"
        elif re.match(r"^\s*원", tail) or last_mult:
            u = "원"
        else:
            u = ""
        out.append({"value": value, "unit": u, "raw": text[start:end].strip()})
        i += 1
    return out


# ---------------------------------------------------------------------------
# 최종 답 추출 (보조 LLM)
# ---------------------------------------------------------------------------
CACHE_FILE = config.VALIDATION_DIR / "_extract_cache.json"
_cache = None

EXTRACT_PROMPT = """질문과 챗봇의 답변이 주어진다. 답변이 질문에 대해 제시한 "최종 답" 숫자 하나를 추출하라.
- 단위 환산: {unit_rule}
- 답변이 답을 거절했거나, 모른다/확인할 수 없다고 했거나, 질문에 대한 숫자를 제시하지 않았으면 abstained=true, value=null.
- 중간 계산값이 아니라 질문이 물은 값을 고른다.
JSON으로만 답한다: {{"value": 숫자 또는 null, "abstained": true 또는 false}}"""

UNIT_RULE = {
    "원": "원 단위 숫자로 바꾼다. 예: '약 1,532만 원' → 15320000, '92.2조 원' → 92200000000000",
    "%": "퍼센트 숫자로 쓴다. 예: '14.47%' → 14.47, '-3.2%' → -3.2",
    None: "답변의 숫자를 그대로 쓴다",
}


def _load_cache():
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    return _cache


def _save_cache():
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")


def heuristic_final(answer: str, unit) -> dict:
    """LLM 없이 쓰는 대체 규칙: 답변 마지막 쪽에서 단위가 맞는 마지막 숫자."""
    if re.search(r"(알 수 없|확인할 수 없|제공할 수 없|모릅니다|답변드리기 어렵|조회되지 않|없습니다\.?$)", answer or ""):
        nums = []
    else:
        nums = [n for n in parse_numbers(answer) if unit is None or n["unit"] == unit]
    if not nums:
        return {"value": None, "abstained": True, "method": "heuristic"}
    return {"value": nums[-1]["value"], "abstained": False, "method": "heuristic"}


def extract_final(question: str, answer: str, unit, client=None, cost=None) -> dict:
    """반환: {"value": float|None, "abstained": bool, "method": "llm"|"heuristic"}"""
    if client is None:
        return heuristic_final(answer, unit)
    cache = _load_cache()
    key = hashlib.sha256(json.dumps([config.AUX_MODEL, question, answer, unit], ensure_ascii=False).encode()).hexdigest()
    if key in cache:
        return cache[key]
    resp = client.chat.completions.create(
        model=config.AUX_MODEL, temperature=0, seed=config.SEED,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": EXTRACT_PROMPT.format(unit_rule=UNIT_RULE.get(unit, UNIT_RULE[None]))},
                  {"role": "user", "content": f"[질문]\n{question}\n\n[답변]\n{answer}"}],
    )
    if cost is not None:
        cost.add(config.AUX_MODEL, resp.usage)
    try:
        parsed = json.loads(resp.choices[0].message.content or "{}")
        value = parsed.get("value")
        value = float(value) if value is not None else None
        out = {"value": value, "abstained": bool(parsed.get("abstained", value is None)), "method": "llm"}
    except (ValueError, TypeError):
        out = heuristic_final(answer, unit)
    cache[key] = out
    _save_cache()
    return out
