"""OpenAI 호출 비용을 누적하고, 예산(BUDGET_USD)을 넘으면 실행을 멈춘다.

누적값은 Results/_cost_log.json에 저장되어 프로그램을 껐다 켜도 이어진다.
팀 키 한도($20)를 넘으면 키가 비활성화되므로, 코드 쪽에서도 상한을 둔다.
"""
import json
import threading

import config

COST_FILE = config.RESULTS_DIR / "_cost_log.json"


class BudgetExceeded(RuntimeError):
    pass


def price_of(model: str) -> tuple:
    for name, p in config.PRICES.items():
        if model == name or model.startswith(name + "-"):
            return p
    return config.DEFAULT_PRICE


class CostTracker:
    """호출마다 add(model, usage)를 부른다. 누적 비용이 예산을 넘으면 BudgetExceeded."""

    def __init__(self, path=COST_FILE, budget=None):
        self.path = path
        self.budget = config.BUDGET_USD if budget is None else budget
        self.lock = threading.Lock()
        self.state = {"total_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "by_model": {}}
        if self.path.exists():
            try:
                self.state.update(json.loads(self.path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass

    @property
    def total(self) -> float:
        return self.state["total_usd"]

    def check(self):
        if self.total >= self.budget:
            raise BudgetExceeded(
                f"누적 비용 ${self.total:.3f}가 상한 ${self.budget:.2f}에 도달해 실행을 멈춥니다. "
                f"계속하려면 config.BUDGET_USD를 올리거나 환경변수 HW2_BUDGET_USD를 설정하세요."
            )

    def add(self, model: str, usage) -> float:
        """usage는 OpenAI 응답의 usage 객체(또는 dict). 이번 호출 비용(USD)을 돌려준다."""
        if usage is None:
            return 0.0
        get = usage.get if isinstance(usage, dict) else (lambda k, d=0: getattr(usage, k, d))
        pt, ct = int(get("prompt_tokens", 0) or 0), int(get("completion_tokens", 0) or 0)
        p_in, p_out = price_of(model)
        cost = pt / 1e6 * p_in + ct / 1e6 * p_out
        with self.lock:
            s = self.state
            s["total_usd"] += cost
            s["prompt_tokens"] += pt
            s["completion_tokens"] += ct
            s["calls"] += 1
            m = s["by_model"].setdefault(model, {"usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0})
            m["usd"] += cost
            m["prompt_tokens"] += pt
            m["completion_tokens"] += ct
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        self.check()
        return cost


_default = None


def default_tracker() -> CostTracker:
    global _default
    if _default is None:
        _default = CostTracker()
    return _default
