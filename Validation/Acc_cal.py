"""채점 지표 계산 (계획서 3.3절).

문항 단위 채점(score_item)과 구성 단위 요약(summarize)으로 나뉜다.
- Acc@1%, Acc@5%: 추출한 최종값과 정답의 상대오차
- 수치 환각률: 답변 속 금액·비율 숫자 중, 정답 근거 숫자(support_values)나 질문 속 숫자 어디에도 없는 숫자의 비율
- 적절한 거절률(F 유형), 부당한 거절률(A–D 유형)
- 멀티턴: 턴 정확도, 시나리오 성공률(답이 필요한 모든 턴이 정답)
- 출처 포함률, 문항당 토큰
"""
import re
from statistics import mean

import config
from Validation.extract import parse_numbers

ANSWER_TYPES = ("A", "B", "C", "D")
_CITE = re.compile(r"(dart\.fss\.or\.kr|DART|전자공시|공공데이터포털|금융위원회|출처)")


def rel_err(pred, gold):
    if pred is None or gold is None:
        return None
    if gold == 0:
        return abs(pred)
    return abs(pred - gold) / abs(gold)


def _supported(value: float, refs: list) -> bool:
    for r in refs:
        if r == 0:
            if abs(value) < 1e-9:
                return True
            continue
        if abs(value - r) / abs(r) <= config.TOL_MAIN:
            return True
        # 비율을 소수로 쓴 경우(0.1447 ↔ 14.47%) 허용
        if abs(value * 100 - r) / abs(r) <= config.TOL_MAIN:
            return True
    return False


def claim_check(answer: str, question: str, support: list) -> tuple:
    """(금액·비율 주장 수, 근거 없는 주장 수, 근거 없는 숫자 목록)"""
    q_nums = [n["value"] for n in parse_numbers(question)]
    refs = list(support) + q_nums
    claims = [n for n in parse_numbers(answer) if n["unit"] in ("원", "%")]
    bad = [n["raw"] for n in claims if not _supported(n["value"], refs)]
    return len(claims), len(bad), bad


def score_item(gold: dict, question: str, answer: str, extracted: dict, trace: dict) -> dict:
    """gold: gold_value, gold_unit, support_values, gold_status, expected 를 가진 dict."""
    status = gold.get("gold_status")
    pred, abstained = extracted.get("value"), extracted.get("abstained", False)
    err = rel_err(pred, gold.get("gold_value")) if status == "ok" else None
    n_claims, n_bad, bad = claim_check(answer, question, gold.get("support_values") or [])
    usage = (trace or {}).get("usage", {})
    return {
        "gold_status": status,
        "gold_value": gold.get("gold_value"),
        "gold_unit": gold.get("gold_unit"),
        "pred_value": pred,
        "abstained": abstained,
        "rel_err": err,
        "correct1": (err is not None and err < config.TOL_MAIN) if status == "ok" else (abstained if status == "abstain" else None),
        "correct5": (err is not None and err < config.TOL_LOOSE) if status == "ok" else (abstained if status == "abstain" else None),
        "n_claims": n_claims,
        "n_unsupported": n_bad,
        "unsupported": "; ".join(bad[:5]),
        "cited": bool(_CITE.search(answer or "")),
        "tokens": usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
        "tools": ",".join(c["name"] for c in (trace or {}).get("tool_calls", [])),
    }


def _rate(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(mean(vals) * 100, 2) if vals else None


def summarize(rows: list) -> dict:
    """rows: 한 구성의 score_item 결과(+ set, type, turn 키). 반환: 지표 이름 → 값(%)."""
    qa = [r for r in rows if r["set"] == "qa"]
    out = {}
    for t in ANSWER_TYPES:
        sub = [r for r in qa if r["type"] == t and r["gold_status"] == "ok"]
        out[f"{t} Acc@1%"] = _rate(sub, "correct1")
    ad = [r for r in qa if r["type"] in ANSWER_TYPES and r["gold_status"] == "ok"]
    out["A-D Acc@1%"] = _rate(ad, "correct1")
    out["A-D Acc@5%"] = _rate(ad, "correct5")

    scored = [r for r in rows if r["set"] in ("qa", "multiturn") and r["gold_status"] in ("ok", "abstain")]
    n_claims = sum(r["n_claims"] for r in scored)
    out["환각률"] = round(sum(r["n_unsupported"] for r in scored) / n_claims * 100, 2) if n_claims else None

    f = [r for r in qa if r["type"] == "F"]
    out["F 적절한 거절률"] = _rate(f, "correct1")
    out["A-D 부당한 거절률"] = _rate([{"x": r["abstained"]} for r in ad], "x")

    mt = [r for r in rows if r["set"] == "multiturn" and r["gold_status"] == "ok"]
    out["E 턴 정확도"] = _rate(mt, "correct1")
    by_sc = {}
    for r in mt:
        by_sc.setdefault(r["id"], []).append(bool(r["correct1"]))
    out["E 시나리오 성공률"] = round(mean(all(v) for v in by_sc.values()) * 100, 2) if by_sc else None

    out["출처 포함률"] = _rate([{"x": r["cited"]} for r in ad], "x")
    toks = [r["tokens"] for r in rows if r["set"] == "qa"]
    out["토큰/문항"] = round(mean(toks), 1) if toks else None
    return out
