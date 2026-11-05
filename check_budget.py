"""OpenAI 팀 키 사용액 확인.

일반(프로젝트) API 키로는 남은 잔액을 조회하는 공식 API가 없다.
- 잔액(balance) 조회 API: 존재하지 않는다.
- 비용(Costs) API: 조직 소유자가 만드는 Admin API 키가 있어야 한다.
- /v1/dashboard/billing/... 주소: 브라우저 로그인 세션 전용이라 코드로 쓸 수 없다.

그래서 이 스크립트는 세 가지를 한다.
  1. 로컬 기록 합산: 이 프로젝트의 챗봇이 호출할 때마다 남긴 비용 기록(Results/_cost_log.json)을 합산해
     한도($20) 대비 사용액을 추정한다. 팀원마다 기록이 따로 쌓이므로 --logs로 여러 파일을 합칠 수 있다.
  2. 키 상태 확인(--ping): 1토큰짜리 요청을 보내 키가 아직 살아 있는지, 한도 초과로 막혔는지 확인한다.
  3. Costs API 시도(--admin-key): Admin 키가 있을 때만 실제 청구 금액을 조회한다.

예시
  python check_budget.py
  python check_budget.py --ping
  python check_budget.py --logs Results/_cost_log.json 팀원A_cost_log.json 팀원B_cost_log.json
  python check_budget.py --admin-key sk-admin-...
"""
import argparse
import json
import time
from pathlib import Path

import requests

import config

TEAM_LIMIT_USD = 20.0
API = "https://api.openai.com/v1"


def local_spend(paths: list) -> tuple:
    """비용 기록 파일들의 합계. 반환: (총액, 파일별 내역)"""
    total, rows = 0.0, []
    for p in paths:
        p = Path(p)
        if not p.exists():
            rows.append((str(p), None, "파일 없음"))
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        usd = float(d.get("total_usd", 0))
        total += usd
        rows.append((str(p), usd, f"호출 {d.get('calls', 0)}회, 입력 {d.get('prompt_tokens', 0):,} / 출력 {d.get('completion_tokens', 0):,} 토큰"))
    return total, rows


def ping(api_key: str) -> str:
    """가장 싼 요청 1회(출력 1토큰)로 키 상태를 확인한다. 비용은 0.0001달러 미만이다."""
    try:
        r = requests.post(f"{API}/chat/completions", timeout=30,
                          headers={"Authorization": f"Bearer {api_key}"},
                          json={"model": config.MODEL, "max_tokens": 1,
                                "messages": [{"role": "user", "content": "hi"}]})
    except requests.RequestException as e:
        return f"요청 실패(네트워크): {e}"
    if r.status_code == 200:
        rem = r.headers.get("x-ratelimit-remaining-tokens")
        return (f"정상: 키가 동작합니다 (모델 {config.MODEL}). "
                f"참고로 분당 남은 토큰 한도는 {rem}이며, 이것은 잔액이 아니라 속도 제한입니다.")
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {"message": r.text[:200]}
    code, msg = err.get("code") or err.get("type"), err.get("message", "")
    if r.status_code == 429 and code == "insufficient_quota":
        return "한도 초과: 사용 한도를 다 써서 키가 막혔습니다. 즉시 사용을 중단하세요."
    if r.status_code == 429:
        return f"속도 제한(잠시 후 재시도): {msg[:150]}"
    if r.status_code == 401:
        return f"인증 실패: 키가 잘못됐거나 비활성화됐습니다. ({msg[:150]})"
    return f"HTTP {r.status_code} {code}: {msg[:200]}"


def org_costs(admin_key: str, days: int = 30) -> str:
    """Costs API (Admin 키 전용). 최근 days일 청구 금액 합계."""
    start = int(time.time()) - days * 86400
    total, page, n = 0.0, None, 0
    while True:
        params = {"start_time": start, "limit": 31}
        if page:
            params["page"] = page
        r = requests.get(f"{API}/organization/costs", params=params, timeout=30,
                         headers={"Authorization": f"Bearer {admin_key}"})
        if r.status_code in (401, 403):
            return (f"HTTP {r.status_code}: 이 키로는 Costs API를 쓸 수 없습니다. "
                    "Costs API는 조직 소유자가 만든 Admin 키(sk-admin-...)가 필요합니다.")
        if r.status_code != 200:
            return f"HTTP {r.status_code}: {r.text[:200]}"
        body = r.json()
        for bucket in body.get("data", []):
            for res in bucket.get("results", []):
                total += float((res.get("amount") or {}).get("value", 0) or 0)
            n += 1
        page = body.get("next_page")
        if not body.get("has_more") or not page:
            break
    return f"최근 {days}일 조직 전체 청구 금액: ${total:.4f} ({n}일치). 다른 팀이 같은 조직을 쓰면 그 사용액도 포함됩니다."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", default=[str(config.RESULTS_DIR / "_cost_log.json")],
                    help="합산할 비용 기록 파일들 (팀원 것 포함)")
    ap.add_argument("--limit", type=float, default=TEAM_LIMIT_USD)
    ap.add_argument("--ping", action="store_true", help="1토큰 요청으로 키 상태 확인")
    ap.add_argument("--admin-key", default=None, help="Admin 키가 있을 때만 실제 청구액 조회")
    args = ap.parse_args()

    print("[1] 로컬 기록 기준 추정 사용액")
    total, rows = local_spend(args.logs)
    for path, usd, info in rows:
        print(f"   {path}: " + (f"${usd:.4f} ({info})" if usd is not None else info))
    used = total / args.limit * 100
    print(f"   합계 ${total:.4f} / 한도 ${args.limit:.2f} → {used:.1f}% 사용, 남은 금액 약 ${args.limit - total:.4f}")
    if used >= 80:
        print("   ⚠ 80% 이상 사용했습니다. 남은 실험 규모를 줄이세요.")
    elif used >= 50:
        print("   ⚠ 50% 이상 사용했습니다.")
    print("   (이 프로젝트 코드가 기록한 호출만 포함됩니다. 다른 코드·다른 팀원의 호출은 --logs로 합치세요.)")

    if args.ping:
        from API_key.API import get_key
        print("\n[2] 키 상태")
        print("   " + ping(get_key("openai")))

    if args.admin_key:
        print("\n[3] Costs API (Admin 키)")
        print("   " + org_costs(args.admin_key))


if __name__ == "__main__":
    main()
