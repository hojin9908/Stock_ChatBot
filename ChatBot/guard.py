"""Tool 3: Safety Guard.

3개 층으로 동작한다 (계획서 5.2절).
1. 입력 분류: 최근 대화를 보고 INFO / ADVICE / PREDICTION / ILLEGAL / DISTRESS 중 하나로 분류한다.
   보조 모델의 JSON 출력 + OpenAI Moderation API(무료)를 함께 쓴다.
2. 출력 검사: 최종 답변에 권유·단정 표현이 있으면 찾아낸다 (챗봇이 1회 재생성한다).
3. 고지 부착: 주가·수익률이 들어간 답변 끝에 고지 문구를 코드가 붙인다.
"""
import json
import re

import config
from Prompt.system_prompts import DISCLAIMER, FORBIDDEN_PATTERNS, GUARD_CATEGORIES, GUARD_CLASSIFIER

_PATTERNS = [re.compile(p) for p in FORBIDDEN_PATTERNS]
# 매치 뒤에 이런 말이 오면 부정문(“확실히 오른다고 말씀드릴 수 없습니다”)이므로 위반이 아니다
_NEGATION = re.compile(r"(수\s*없|않|어렵|말씀드리기|단정할|라고\s*(말|단정|권유)|지는\s*알\s*수|드릴\s*수\s*없)")

PRICE_TOOLS = {"get_price", "get_price_series", "simulate"}
_RETURN_WORDS = re.compile(r"(수익률|평가액|평가 금액|투자했다면|넣었다면|원금)")


def _recent_dialogue(messages: list, max_msgs: int = 8) -> str:
    lines = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and content:
            lines.append(f"{'사용자' if role == 'user' else '챗봇'}: {str(content)[:500]}")
    return "\n".join(lines[-max_msgs:])


def classify(client, messages: list, cost=None) -> dict:
    """반환: {"category": "ADVICE", "reason": "...", "moderation_flagged": False}"""
    dialogue = _recent_dialogue(messages)
    result = {"category": "INFO", "reason": "", "moderation_flagged": False}
    try:
        resp = client.chat.completions.create(
            model=config.AUX_MODEL,
            temperature=0,
            seed=config.SEED,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": GUARD_CLASSIFIER},
                      {"role": "user", "content": dialogue}],
        )
        if cost is not None:
            cost.add(config.AUX_MODEL, resp.usage)
        parsed = json.loads(resp.choices[0].message.content or "{}")
        cat = str(parsed.get("category", "INFO")).upper()
        result["category"] = cat if cat in GUARD_CATEGORIES else "INFO"
        result["reason"] = str(parsed.get("reason", ""))[:200]
    except Exception as e:  # 분류 실패 시 INFO로 두고 출력 검사에 맡긴다
        if type(e).__name__ == "BudgetExceeded":
            raise
        result["reason"] = f"classifier_error: {type(e).__name__}"

    # Moderation API: 일반 유해성(폭력, 자해 등). 무료이며 실패해도 무시한다.
    last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user" and m.get("content")), "")
    try:
        mod = client.moderations.create(model="omni-moderation-latest", input=str(last_user)[:2000])
        r = mod.results[0]
        result["moderation_flagged"] = bool(r.flagged)
        if r.flagged:
            cats = r.categories.model_dump() if hasattr(r.categories, "model_dump") else dict(r.categories)
            if any(v for k, v in cats.items() if k.startswith("self")):
                result["category"] = "DISTRESS"
            elif result["category"] == "INFO":
                result["category"] = "ILLEGAL"
    except Exception:
        pass
    return result


def check_output(text: str) -> list:
    """답변 속 권유·단정 표현 목록. 바로 뒤에 부정 표현이 오면 제외한다."""
    found = []
    text = text or ""
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            tail = text[m.end(): m.end() + 20]
            if _NEGATION.search(tail):
                continue
            found.append(m.group(0))
    return found


def attach_disclaimer(text: str, used_tools: set) -> str:
    """주가·수익률을 다룬 답변 끝에 고지 문구를 붙인다. 이미 있으면 붙이지 않는다."""
    text = text or ""
    if DISCLAIMER in text:
        return text
    if (used_tools & PRICE_TOOLS) or _RETURN_WORDS.search(text):
        return text.rstrip() + "\n\n" + DISCLAIMER
    return text
