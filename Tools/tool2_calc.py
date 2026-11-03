"""Tool 2: 숫자를 인자로 받아 계산 결과만 돌려주는 도구.

데이터를 직접 조회하지 않는다. 입력 숫자가 어디서 왔는지(Tool 1 결과인지, LLM의 기억인지)는
이 파일이 알 수 없고 알 필요도 없다. 그래서 "Tool 2만 켜진 구성(C2)"은
"계산은 정확하지만 입력이 틀릴 수 있는 경우"를 보여준다.

모든 함수는 예외 대신 {"error": "..."}를 돌려준다.
"""
import ast
import math
import operator


def _num(x, name):
    if isinstance(x, bool):
        raise ValueError(f"{name}는 숫자여야 합니다.")
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        raise ValueError(f"{name}를 숫자로 해석할 수 없습니다: {x}")


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
            return {"error": f"{type(e).__name__}: {e}"}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@_safe
def ratio(numerator, denominator, as_percent: bool = True) -> dict:
    """numerator ÷ denominator. as_percent면 ×100 (부채비율, ROE, 영업이익률 등)."""
    n, d = _num(numerator, "numerator"), _num(denominator, "denominator")
    if d == 0:
        raise ZeroDivisionError("분모가 0입니다.")
    v = n / d * (100 if as_percent else 1)
    return {"result": round(v, 4), "unit": "%" if as_percent else "배", "formula": f"{n} / {d}" + (" × 100" if as_percent else "")}


@_safe
def growth(old_value, new_value) -> dict:
    """증감률(%) = (new − old) ÷ |old| × 100."""
    o, n = _num(old_value, "old_value"), _num(new_value, "new_value")
    if o == 0:
        raise ZeroDivisionError("이전 값이 0이라 증감률을 정의할 수 없습니다.")
    return {"result": round((n - o) / abs(o) * 100, 4), "unit": "%", "formula": f"({n} − {o}) / |{o}| × 100"}


@_safe
def cagr(start_value, end_value, years) -> dict:
    """연평균 성장률(%) = (end ÷ start)^(1/years) − 1."""
    s, e, y = _num(start_value, "start_value"), _num(end_value, "end_value"), _num(years, "years")
    if y <= 0:
        raise ValueError("years는 0보다 커야 합니다.")
    if s <= 0 or e <= 0:
        raise ValueError("시작값과 끝값이 모두 양수여야 CAGR을 계산할 수 있습니다.")
    return {"result": round(((e / s) ** (1 / y) - 1) * 100, 4), "unit": "%", "formula": f"({e} / {s})^(1/{y}) − 1"}


@_safe
def select_year(table: list, rule: str) -> dict:
    """[{"year": 2022, "value": 25.1}, ...]에서 value가 최소(rule="min") 또는 최대(rule="max")인 연도.
    값이 같으면 이른 연도를 고른다."""
    if rule not in ("min", "max"):
        raise ValueError("rule은 'min' 또는 'max'입니다.")
    rows = [(int(r["year"]), _num(r["value"], "value")) for r in table if r.get("value") is not None]
    if not rows:
        raise ValueError("비교할 값이 없습니다.")
    rows.sort(key=lambda t: t[0])
    best = min(rows, key=lambda t: t[1]) if rule == "min" else max(rows, key=lambda t: t[1])
    return {"year": best[0], "value": best[1], "rule": rule}


@_safe
def simulate(buy_prices: list, amount_per_buy, valuation_price) -> dict:
    """같은 금액을 여러 번(또는 한 번) 매수했을 때의 평가액과 수익률.

    거치식: buy_prices에 가격 1개.  적립식: 매월 매수 가격들.
    소수점 주식 매수를 허용하고 수수료·세금·배당은 반영하지 않는다.
    """
    prices = [_num(p, "buy_prices") for p in buy_prices]
    if not prices or any(p <= 0 for p in prices):
        raise ValueError("buy_prices는 양수 가격 1개 이상이어야 합니다.")
    a, v = _num(amount_per_buy, "amount_per_buy"), _num(valuation_price, "valuation_price")
    shares = sum(a / p for p in prices)
    invested = a * len(prices)
    final = shares * v
    return {
        "num_buys": len(prices),
        "invested": round(invested, 2),
        "shares": round(shares, 6),
        "final_value": round(final, 2),
        "profit": round(final - invested, 2),
        "return_pct": round((final - invested) / invested * 100, 4),
        "unit": "원",
    }


_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("거듭제곱 지수가 너무 큽니다.")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("사칙연산(+ − × ÷), 거듭제곱(**), 괄호, 숫자만 쓸 수 있습니다.")


@_safe
def calculator(expression: str) -> dict:
    """사칙연산·거듭제곱 식을 계산한다. eval을 쓰지 않고 구문 트리를 직접 해석해 임의 코드 실행을 막는다."""
    expr = str(expression).replace(",", "").replace("×", "*").replace("÷", "/").replace("^", "**")
    if len(expr) > 300:
        raise ValueError("식이 너무 깁니다.")
    value = _eval_node(ast.parse(expr, mode="eval"))
    if math.isnan(value) or math.isinf(value):
        raise ValueError("결과가 유한한 수가 아닙니다.")
    return {"result": round(value, 6), "expression": expression}
