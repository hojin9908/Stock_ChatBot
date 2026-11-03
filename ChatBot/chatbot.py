"""챗봇 본체.

설정값 3개(tool1, tool2, tool3)를 받아 만들어지며, 세 값이 모두 False면 Vanilla(C0)다.
8개 구성이 모두 이 클래스 하나를 쓰므로 프롬프트·모델·대화 처리 방식이 같다.

한 턴의 처리 순서
  (1) Tool 3이 켜져 있으면 질문을 분류하고, 위험 범주면 그 턴에만 안전 정책 지시를 덧붙인다.
  (2) 대화 이력 + 켜진 도구 설명서로 OpenAI를 호출한다.
  (3) 모델이 도구를 요청하면 파이썬이 실행하고 결과를 이력에 추가한다.
  (4) 모델이 도구를 더 요청하지 않을 때까지 (2)–(3)을 반복한다 (최대 MAX_TOOL_STEPS회).
  (5) Tool 3이 켜져 있으면 답변의 권유·단정 표현을 검사해 1회 재생성하고, 고지 문구를 붙인다.
"""
import json
import time

import config
from ChatBot import guard
from ChatBot.logger import default_tracker
from Prompt.system_prompts import GUARD_POLICY, GUARD_REGENERATE, build_system_prompt
from Tools.schemas import build_tools


def config_id(tool1: bool, tool2: bool, tool3: bool) -> str:
    for cid, flags in config.CONFIGS.items():
        if flags == (tool1, tool2, tool3):
            return cid
    return "custom"


class StockChatbot:
    def __init__(self, tool1=False, tool2=False, tool3=False, *, model=None, client=None, cost=None):
        self.tool1, self.tool2, self.tool3 = bool(tool1), bool(tool2), bool(tool3)
        self.cid = config_id(self.tool1, self.tool2, self.tool3)
        self.model = model or config.MODEL
        if client is None:
            from openai import OpenAI
            from API_key.API import get_key
            client = OpenAI(api_key=get_key("openai"))
        self.client = client
        self.cost = cost or default_tracker()
        self.tool_specs, self.tool_funcs = build_tools(self.tool1, self.tool2)
        self.system_prompt = build_system_prompt(self.tool1, self.tool2)
        self.reset()

    # ------------------------------------------------------------------
    def reset(self):
        """대화 이력을 비운다. 평가에서는 문항(시나리오)마다 부른다."""
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def _call(self, messages, use_tools=True):
        kwargs = dict(model=self.model, messages=messages, temperature=config.TEMPERATURE, seed=config.SEED)
        if use_tools and self.tool_specs:
            kwargs["tools"] = self.tool_specs
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        self.cost.add(self.model, resp.usage)
        return resp

    def _run_tool(self, name, arguments_json):
        func = self.tool_funcs.get(name)
        if func is None:
            return {"error": f"사용할 수 없는 도구입니다: {name}"}
        try:
            args = json.loads(arguments_json or "{}")
        except json.JSONDecodeError:
            return {"error": "도구 인자가 올바른 JSON이 아닙니다."}
        try:
            return func(**args)
        except TypeError as e:
            return {"error": f"인자 오류: {e}"}
        except Exception as e:  # 도구 내부 오류도 모델에게 알려 대처하게 한다
            return {"error": f"{type(e).__name__}: {e}"}

    # ------------------------------------------------------------------
    def chat(self, user_text: str) -> tuple:
        """사용자 발화 하나를 처리하고 (최종 답변, trace)를 돌려준다."""
        t0 = time.time()
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        trace = {"config": self.cid, "user": user_text, "guard": None, "tool_calls": [],
                 "steps": 0, "violations": [], "regenerated": False, "disclaimer": False}

        self.messages.append({"role": "user", "content": user_text})

        # (1) Tool 3 입력 분류 → 이 턴에만 쓰는 정책 지시
        turn_extra = []
        if self.tool3:
            g = guard.classify(self.client, self.messages, self.cost)
            trace["guard"] = g
            policy = GUARD_POLICY.get(g["category"])
            if policy:
                turn_extra.append({"role": "system", "content": policy})

        def add_usage(resp):
            if resp.usage is not None:
                usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
                usage["completion_tokens"] += resp.usage.completion_tokens or 0

        # (2)–(4) 도구 호출 반복
        answer = None
        for step in range(config.MAX_TOOL_STEPS):
            resp = self._call(self.messages + turn_extra)
            add_usage(resp)
            msg = resp.choices[0].message
            trace["steps"] = step + 1
            if not msg.tool_calls:
                answer = msg.content or ""
                break
            self.messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                               for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                result = self._run_tool(tc.function.name, tc.function.arguments)
                trace["tool_calls"].append({"name": tc.function.name, "arguments": tc.function.arguments,
                                            "result": result})
                content = json.dumps(result, ensure_ascii=False)[: config.TOOL_RESULT_MAX_CHARS]
                self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
        if answer is None:  # 반복 한도를 넘으면 도구 없이 답만 받는다
            resp = self._call(self.messages + turn_extra, use_tools=False)
            add_usage(resp)
            answer = resp.choices[0].message.content or ""
            trace["hit_step_limit"] = True

        # (5) Tool 3 출력 검사와 고지
        if self.tool3:
            found = guard.check_output(answer)
            if found:
                trace["violations"] = found
                trace["draft_answer"] = answer
                regen = {"role": "system", "content": GUARD_REGENERATE.format(phrases=", ".join(sorted(set(found))))}
                resp = self._call(self.messages + turn_extra + [regen], use_tools=False)
                add_usage(resp)
                answer = resp.choices[0].message.content or answer
                trace["regenerated"] = True
                trace["violations_after"] = guard.check_output(answer)
            used = {c["name"] for c in trace["tool_calls"]}
            new_answer = guard.attach_disclaimer(answer, used)
            trace["disclaimer"] = new_answer != answer
            answer = new_answer

        self.messages.append({"role": "assistant", "content": answer})
        trace["answer"] = answer
        trace["usage"] = usage
        trace["elapsed_sec"] = round(time.time() - t0, 2)
        return answer, trace
