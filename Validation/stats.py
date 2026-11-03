"""통계 검정.

- mcnemar: 같은 문항에 대한 두 구성의 정답 여부(짝지은 0/1)를 비교하는 정확 검정.
  두 구성 중 한쪽만 맞힌 문항 수 b(A만 정답), c(B만 정답)로 이항검정을 한다.
- bootstrap_ci: 정답률의 95% 신뢰구간.
- cohen_kappa: 라벨러 2명의 일치도 (Safety 라벨, 오류 원인 라벨).
"""
import random
from collections import Counter
from math import comb


def mcnemar(a: list, b: list) -> dict:
    """a, b: 같은 순서의 True/False 목록. 반환: b_only, c_only, p_value(양측)."""
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    n = only_a + only_b
    if n == 0:
        return {"only_a": 0, "only_b": 0, "p_value": 1.0}
    k = min(only_a, only_b)
    p = sum(comb(n, i) for i in range(0, k + 1)) / 2 ** n * 2
    return {"only_a": only_a, "only_b": only_b, "p_value": round(min(1.0, p), 4)}


def bootstrap_ci(values: list, n_boot: int = 2000, seed: int = 0, alpha: float = 0.05) -> tuple:
    """0/1 목록의 평균(%)과 신뢰구간(%)."""
    vals = [1.0 if v else 0.0 for v in values]
    if not vals:
        return None, None, None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(n_boot))
    lo = means[int(alpha / 2 * n_boot)]
    hi = means[int((1 - alpha / 2) * n_boot) - 1]
    return round(sum(vals) / len(vals) * 100, 2), round(lo * 100, 2), round(hi * 100, 2)


def cohen_kappa(l1: list, l2: list):
    pairs = [(x, y) for x, y in zip(l1, l2) if x not in (None, "") and y not in (None, "")]
    if not pairs:
        return None
    n = len(pairs)
    po = sum(x == y for x, y in pairs) / n
    c1, c2 = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    pe = sum(c1[k] * c2[k] for k in set(c1) | set(c2)) / n ** 2
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0
