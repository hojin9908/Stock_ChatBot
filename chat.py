"""터미널에서 챗봇과 직접 대화한다. 데모 녹화와 디버깅용.

예시
  python chat.py --config C0                 # Vanilla
  python chat.py --config C7 --show-trace    # Full + 도구 호출 과정 표시
  python chat.py --tool1 --tool2             # 구성 ID 대신 기능을 직접 지정

대화 중 명령: /reset (대화 초기화), /cost (누적 비용), /exit (종료)
"""
import argparse
import json

import config
from ChatBot.chatbot import StockChatbot


def show_trace(trace: dict):
    g = trace.get("guard")
    if g:
        print(f"  [Tool 3 분류] {g['category']} ({g.get('reason', '')})")
    for c in trace["tool_calls"]:
        res = json.dumps(c["result"], ensure_ascii=False)
        print(f"  [도구] {c['name']}({c['arguments']})")
        print(f"         → {res[:300]}{'…' if len(res) > 300 else ''}")
    if trace.get("violations"):
        print(f"  [Tool 3 출력 검사] 권유·단정 표현 {trace['violations']} → 재생성")
    u = trace["usage"]
    print(f"  [토큰] 입력 {u['prompt_tokens']}, 출력 {u['completion_tokens']}, {trace['elapsed_sec']}초")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=list(config.CONFIGS), default=None)
    ap.add_argument("--tool1", action="store_true")
    ap.add_argument("--tool2", action="store_true")
    ap.add_argument("--tool3", action="store_true")
    ap.add_argument("--show-trace", action="store_true")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    flags = config.CONFIGS[args.config] if args.config else (args.tool1, args.tool2, args.tool3)
    bot = StockChatbot(*flags, model=args.model)
    print(f"구성 {bot.cid} (Tool1={flags[0]}, Tool2={flags[1]}, Tool3={flags[2]}), 모델 {bot.model}, "
          f"기준일 {config.REF_DATE}. /exit로 종료")
    while True:
        try:
            user = input("\n사용자> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user == "/exit":
            break
        if user == "/reset":
            bot.reset()
            print("(대화를 초기화했습니다)")
            continue
        if user == "/cost":
            print(f"(누적 비용 ${bot.cost.total:.4f})")
            continue
        answer, trace = bot.chat(user)
        if args.show_trace:
            show_trace(trace)
        print(f"\n챗봇> {answer}")


if __name__ == "__main__":
    main()
