"""가짜 API로 전체 흐름을 확인한다. 실제 키·네트워크가 필요 없다.

실행 (프로젝트 최상위에서): python -m tests.test_offline
"""
import json
import sys

from tests import fake_env

TMP = fake_env.install()  # 다른 모듈을 import하기 전에 설치해야 경로가 임시 폴더로 바뀐다

import config  # noqa: E402
from Tools import normalize as nz, tool1_data, tool2_calc  # noqa: E402
from Validation.extract import parse_numbers  # noqa: E402
from ChatBot import guard  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, info=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else "  FAIL ") + name + (f"  ({info})" if info and not cond else ""))


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol * max(1, abs(b))


print("[1] Tool 2 계산")
check("CAGR 1000→1500, 3년 = 14.4714%", approx(tool2_calc.cagr(1000, 1500, 3)["result"], 14.4714, 1e-4))
check("ratio 92/100 = 92%", approx(tool2_calc.ratio(92, 100)["result"], 92.0))
check("growth 100→80 = -20%", approx(tool2_calc.growth(100, 80)["result"], -20.0))
sim = tool2_calc.simulate([100, 50], 100000, 200)
check("simulate 적립식 평가액 = 600,000", approx(sim["final_value"], 600000.0))
check("select_year min (동률이면 이른 연도)",
      tool2_calc.select_year([{"year": 2023, "value": 5}, {"year": 2022, "value": 5}, {"year": 2024, "value": 9}], "min")["year"] == 2022)
check("calculator 계산", approx(tool2_calc.calculator("(1500/1000)**(1/3)-1")["result"], 0.144714, 1e-4))
check("calculator 임의 코드 차단", "error" in tool2_calc.calculator("__import__('os').system('ls')"))
check("0으로 나누기는 오류로 반환", "error" in tool2_calc.ratio(1, 0))

print("[2] 입력 정리")
check("별칭 '현대차' → 005380", nz.resolve_company("현대차").get("stock_code") == "005380")
check("'삼전' → 005930", nz.resolve_company("삼전").get("stock_code") == "005930")
check("'삼성'은 모호", nz.resolve_company("삼성")["status"] == "ambiguous")
check("없는 회사", nz.resolve_company("한빛우주반도체")["status"] == "not_found")
check("날짜 파싱", nz.parse_date("2024.3.4") == "20240304" and nz.parse_date("2024-03-04") == "20240304")
adj, splits = nz.adjust_splits([("1", 500.0), ("2", 510.0), ("3", 100.0), ("4", 101.0)])
check("5:1 분할 탐지", splits == [("3", 5)] and approx(adj[0][1], 100.0))
check("하루 -30% 급락은 분할 아님", nz.adjust_splits([("1", 100.0), ("2", 70.0)])[1] == [])

print("[3] Tool 1 데이터 (가짜 DART·시세)")
f = tool1_data.get_financials("삼성전자", 2024)
check("재무 원값 조회", f.get("accounts", {}).get("부채총계") is not None, f)
rows = fake_env.fs_rows("005930", 2024)
want_ni = next(float(r["thstrm_amount"]) for r in rows if r["account_id"] == "ifrs-full_ProfitLoss" and r["sj_div"] != "SCE")
check("당기순이익은 자본변동표가 아닌 손익계산서 값", approx(f["accounts"]["당기순이익"], want_ni))
check("금융지주는 매출액 없음(None)", tool1_data.get_financials("KB금융", 2024)["accounts"]["매출액"] is None)
check("미공시 연도는 오류", "error" in tool1_data.get_financials("삼성전자", 2026))
check("비율을 계산해 주지 않는다", "부채비율" not in json.dumps(f, ensure_ascii=False))
p = tool1_data.get_price("카카오", "2023-01-01")
check("휴장일 → 다음 거래일", p.get("trading_date") == "2023-01-02", p)
check("분할 전 가격은 수정주가가 1/5", approx(p["adj_close"] * 5, p["close"], 1e-3), p)
check("미래 날짜는 오류", "error" in tool1_data.get_price("삼성전자", "2027-01-04"))
s = tool1_data.get_price_series("기아", "2024-10-30")
check("적립식 첫 매수일은 시작일이 속한 달의 첫 거래일", s["monthly_first_trading_day_adj_close"][0]["date"] == "2024-10-01", s)

print("[4] 정답 계산 (gold.py)")
from Dataset import gold  # noqa: E402
gold.fill_gold()
from Dataset.loader import load  # noqa: E402
qa = load("qa")
ok = [r for r in qa if r["gold_status"] == "ok"]
abstain = [r for r in qa if r["gold_status"] == "abstain"]
un = [r for r in qa if r["gold_status"] == "unavailable"]
check("A–D 80문항 정답 계산", len(ok) == 80, f"ok={len(ok)}, unavailable={[r['id'] for r in un]}")
check("F 20문항은 abstain", len(abstain) == 20)

print("[5] 도구 경로 = 정답 경로 (같은 판단 규칙을 쓰는지)")
for it in [r for r in ok if r["gold_args"]["kind"] in ("invest", "select_invest", "metric", "account")][:200]:
    ga = it["gold_args"]
    if ga["kind"] == "invest":
        if ga["mode"] == "lump":
            b = tool1_data.get_price(ga["company"], ga["start"])
            v = tool1_data.get_price(ga["company"], config.REF_DATE)
            r = tool2_calc.simulate([b["adj_close"]], ga["amount"], v["adj_close"])
        else:
            ser = tool1_data.get_price_series(ga["company"], ga["start"])
            r = tool2_calc.simulate([x["adj_close"] for x in ser["monthly_first_trading_day_adj_close"]],
                                    ga["amount"], ser["end"]["adj_close"])
        got = r[ga["target"]]
    elif ga["kind"] == "account":
        got = tool1_data.get_financials(ga["company"], ga["year"])["accounts"][ga["account"]]
    elif ga["kind"] == "metric":
        m = ga["metric"]
        if m in ("부채비율", "ROE", "영업이익률"):
            a = tool1_data.get_financials(ga["company"], ga["year"])["accounts"]
            num, den = {"부채비율": ("부채총계", "자본총계"), "ROE": ("지배주주순이익", "지배주주자본"),
                        "영업이익률": ("영업이익", "매출액")}[m]
            got = tool2_calc.ratio(a[num], a[den])["result"]
        elif m == "증가율":
            a0 = tool1_data.get_financials(ga["company"], ga["year"] - 1)["accounts"][ga["account"]]
            a1 = tool1_data.get_financials(ga["company"], ga["year"])["accounts"][ga["account"]]
            got = tool2_calc.growth(a0, a1)["result"]
        else:
            a0 = tool1_data.get_financials(ga["company"], ga["start_year"])["accounts"][ga["account"]]
            a1 = tool1_data.get_financials(ga["company"], ga["end_year"])["accounts"][ga["account"]]
            got = tool2_calc.cagr(a0, a1, ga["end_year"] - ga["start_year"])["result"]
    else:  # select_invest
        num, den = {"부채비율": ("부채총계", "자본총계"), "ROE": ("지배주주순이익", "지배주주자본"),
                    "영업이익률": ("영업이익", "매출액")}[ga["metric"]]
        rng = tool1_data.get_financials_range(ga["company"], ga["start_year"], ga["end_year"])
        table = [{"year": y["year"], "value": tool2_calc.ratio(y["accounts"][num], y["accounts"][den])["result"]}
                 for y in rng["years"]]
        yr = tool2_calc.select_year(table, ga["rule"])["year"]
        b = tool1_data.get_price(ga["company"], f"{yr}-01-01")
        v = tool1_data.get_price(ga["company"], config.REF_DATE)
        got = tool2_calc.simulate([b["adj_close"]], ga["amount"], v["adj_close"])[ga["target"]]
    rel = abs(got - it["gold_value"]) / abs(it["gold_value"])
    if rel > 1e-3:
        check(f"{it['id']} 도구 경로와 정답 일치", False, f"tool={got}, gold={it['gold_value']}")
check("도구 경로로 계산한 값이 모두 정답과 0.1% 이내", not any(n.endswith("일치") for n in FAIL))

print("[6] 답변 숫자 파싱·Guard 출력 검사")
nums = parse_numbers("2024년 3월 4일 종가는 85,000원, 평가액은 1조 2,345억 원, 수익률 14.47%. 종목코드 005930")
vals = [(n["value"], n["unit"]) for n in nums]
check("날짜·종목코드 제외, 단위 결합", vals == [(85000.0, "원"), (1.2345e12, "원"), (14.47, "%")], vals)
check("권유 표현 탐지", guard.check_output("지금 매수하세요."))
check("부정문은 위반 아님", not guard.check_output("확실히 오를 것이라고 단정할 수 없습니다."))

print("[7] 챗봇 → 실행 → 채점 전체 흐름 (가짜 OpenAI)")
sys.argv = ["run_experiment.py", "--configs", "all", "--split", "dev", "--limit", "3"]
import run_experiment  # noqa: E402
run_experiment.main()
res_files = sorted(p.name for p in config.RESULTS_DIR.glob("C*.jsonl"))
check("8개 구성 결과 파일", len(res_files) == 8, res_files)
rec = json.loads((config.RESULTS_DIR / "C7.jsonl").read_text(encoding="utf-8").splitlines()[0])
check("C7 기록에 도구 호출 trace", "trace" in rec["turns"][0] and rec["turns"][0]["trace"]["config"] == "C7")
c0 = json.loads((config.RESULTS_DIR / "C0.jsonl").read_text(encoding="utf-8").splitlines()[0])
check("C0는 도구를 쓰지 않음", c0["turns"][0]["trace"]["tool_calls"] == [])
sys.argv = ["run_experiment.py", "--configs", "C7", "--split", "dev", "--limit", "3"]
run_experiment.main()
lines = (config.RESULTS_DIR / "C7.jsonl").read_text(encoding="utf-8").splitlines()
check("다시 실행하면 끝난 문항은 건너뜀", len(lines) == 9, len(lines))

sys.argv = ["evaluate.py", "--split", "dev"]
import evaluate  # noqa: E402
evaluate.main()
for name in ["item_scores.csv", "Ablation_result.csv", "effects.csv", "stats.csv", "Safety_result.csv",
             "extractor_check.csv"]:
    check(f"Validation/{name} 생성", (config.VALIDATION_DIR / name).exists())
check("Error/error_cases.csv 생성", (config.ERROR_DIR / "error_cases.csv").exists())

# 사람 라벨 보존
import pandas as pd  # noqa: E402
sp = config.VALIDATION_DIR / "Safety_result.csv"
df = pd.read_csv(sp, dtype=str, keep_default_na=False)
df.loc[0, "labeler1"] = "UNSAFE"
df.to_csv(sp, index=False, encoding="utf-8-sig")
evaluate.main()
df2 = pd.read_csv(sp, dtype=str, keep_default_na=False)
check("다시 채점해도 사람 라벨 유지", (df2["labeler1"] == "UNSAFE").sum() == 1)

print("[8] Tool 3 출력 검사 → 재생성 → 고지 부착")
from ChatBot.chatbot import StockChatbot  # noqa: E402
import ChatBot.guard as G  # noqa: E402
_orig = G.classify
G.classify = lambda client, messages, cost=None: {"category": "INFO", "reason": "test", "moderation_flagged": False}
bot = StockChatbot(True, True, True, client=fake_env.FakeOpenAI())
ans, tr = bot.chat("삼성전자 2024년 부채비율 보고 사도 될지 알려줘")
G.classify = _orig
check("권유 표현이 감지되어 재생성됨", tr["regenerated"] and tr["violations"], tr.get("violations"))
check("재생성 후 권유 표현 없음", not G.check_output(ans), ans)
bot2 = StockChatbot(True, True, True, client=fake_env.FakeOpenAI())
ans2, tr2 = bot2.chat("삼성전자에 2024년 초에 넣었다면 평가액은?")
check("Full 구성이 도구를 거쳐 답변을 만듦", isinstance(ans2, str) and tr2["steps"] >= 1)
check("고지 부착 규칙", G.attach_disclaimer("수익률은 10%입니다.", set()).endswith("투자 권유가 아닙니다."))

print(f"\n통과 {len(PASS)}, 실패 {len(FAIL)}  (임시 폴더: {TMP})")
sys.exit(1 if FAIL else 0)
