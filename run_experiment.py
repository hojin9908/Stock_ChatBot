"""실행: 선택한 구성 × 문항을 챗봇에 넣고 Results/{구성}.jsonl에 기록한다.

- 문항(또는 멀티턴 시나리오)마다 챗봇 대화 이력을 초기화한다.
- 멀티턴 시나리오는 턴 순서대로 같은 대화에 넣는다.
- 중간에 멈춰도 다시 실행하면 이미 성공한 문항은 건너뛰고 이어서 돌린다.
- 누적 비용이 config.BUDGET_USD를 넘으면 즉시 멈춘다.

예시
  python run_experiment.py                                  # 8개 구성 × 전체 문항
  python run_experiment.py --configs C7 --sets safety
  python run_experiment.py --configs C0 C3 C7 --limit 3     # 비용 확인용 소규모 실행
"""
import argparse
import json
import time

import config
from ChatBot.chatbot import StockChatbot
from ChatBot.logger import BudgetExceeded, default_tracker
from Dataset.loader import load

SETS = ("qa", "multiturn", "safety")


def items_of(set_name: str) -> list:
    """모든 세트를 "턴 목록"을 가진 같은 형태로 맞춘다."""
    out = []
    for it in load(set_name):
        if set_name == "qa":
            turns = [it["question"]]
        elif set_name == "multiturn":
            turns = [t["user"] for t in it["turns"]]
        else:
            turns = it["turns"]
        out.append({"id": it["id"], "type": it["type"], "turns": turns})
    return out


def done_ids(path) -> set:
    """이미 오류 없이 끝난 (set, id). 같은 문항이 여러 번 있으면 마지막 기록을 따른다."""
    status = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                status[(r["set"], r["id"])] = "error" not in r
    return {k for k, ok in status.items() if ok}


def run_one(bot: StockChatbot, item: dict) -> dict:
    bot.reset()
    rec = {"id": item["id"], "type": item["type"], "turns": []}
    for user in item["turns"]:
        answer, trace = bot.chat(user)
        rec["turns"].append({"user": user, "answer": answer, "trace": trace})
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="+", default=["all"], help="C0 … C7 또는 all")
    ap.add_argument("--sets", nargs="+", default=list(SETS), choices=SETS)
    ap.add_argument("--limit", type=int, default=0, help="세트별 앞에서 N개만 (시험 실행용)")
    ap.add_argument("--model", default=None, help="config.MODEL 대신 쓸 모델")
    ap.add_argument("--rerun", action="store_true",
                    help="이미 끝난 문항도 다시 실행. 채점은 마지막 기록을 쓴다")
    args = ap.parse_args()

    cids = list(config.CONFIGS) if args.configs == ["all"] else args.configs
    cost = default_tracker()
    config.RESULTS_DIR.mkdir(exist_ok=True)
    print(f"구성 {cids}, 세트 {args.sets}, 현재 누적 비용 ${cost.total:.3f}")

    try:
        for cid in cids:
            t1, t2, t3 = config.CONFIGS[cid]
            bot = StockChatbot(t1, t2, t3, model=args.model, cost=cost)
            path = config.RESULTS_DIR / f"{cid}.jsonl"
            done = set() if args.rerun else done_ids(path)
            for set_name in args.sets:
                items = items_of(set_name)
                if args.limit:
                    items = items[: args.limit]
                todo = [it for it in items if (set_name, it["id"]) not in done]
                print(f"[{cid}] {set_name}: {len(todo)}/{len(items)}개 실행")
                for it in todo:
                    t0 = time.time()
                    try:
                        rec = run_one(bot, it)
                    except BudgetExceeded:
                        raise
                    except Exception as e:  # 한 문항의 오류로 전체를 멈추지 않는다. 다음 실행 때 다시 시도된다.
                        rec = {"id": it["id"], "type": it["type"], "turns": [],
                               "error": f"{type(e).__name__}: {e}"}
                        print(f"   ! {it['id']} 오류: {rec['error'][:150]}")
                    rec.update({"config": cid, "set": set_name, "model": bot.model,
                                "elapsed_sec": round(time.time() - t0, 2)})
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"   누적 비용 ${cost.total:.3f}")
    except BudgetExceeded as e:
        print(f"\n중단: {e}")


if __name__ == "__main__":
    main()
