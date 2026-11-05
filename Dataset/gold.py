"""정답 값을 계산해 qa.jsonl과 multiturn.jsonl의 gold_value 칸을 채운다.

정답의 독립성 (계획서 3.2절)
- 이 파일은 Tools/를 import하지 않는다. 회사명 해석, 계정 추출, 액면분할 보정, 투자 계산을 모두 따로 구현한다.
  같은 함수를 쓰면 그 함수의 버그가 시스템과 정답에 똑같이 들어가 오류가 보이지 않기 때문이다.
- 원자료(Data_call)만 시스템과 공유한다. 원자료가 같아야 공정한 비교가 되기 때문이다.
- 팀 규칙: Tool을 작성한 사람이 아닌 다른 팀원이 이 파일을 검토(또는 다시 작성)한다.

이 파일은 실제 API를 호출하므로 .env에 키가 있어야 하며, 실행하면 캐시도 함께 채워진다.
실행: python -m Dataset.gold
"""
import math

import pandas as pd

import config
from Data_call.Dart_call import fetch_financial_statement, load_corp_codes
from Data_call.Price_call import fetch_daily_prices
from Dataset.loader import load, write_jsonl

FILES = {"qa": config.DATASET_DIR / "qa.jsonl", "multiturn": config.DATASET_DIR / "multiturn.jsonl"}

# 계정 → (표준계정 ID, 재무제표 구분)
GOLD_ACCOUNTS = {
    "매출액": ("ifrs-full_Revenue", ("IS", "CIS")),
    "영업이익": ("dart_OperatingIncomeLoss", ("IS", "CIS")),
    "당기순이익": ("ifrs-full_ProfitLoss", ("IS", "CIS")),
    "지배주주순이익": ("ifrs-full_ProfitLossAttributableToOwnersOfParent", ("IS", "CIS")),
    "자산총계": ("ifrs-full_Assets", ("BS",)),
    "부채총계": ("ifrs-full_Liabilities", ("BS",)),
    "자본총계": ("ifrs-full_Equity", ("BS",)),
    "지배주주자본": ("ifrs-full_EquityAttributableToOwnersOfParent", ("BS",)),
}
# 표준계정 ID가 없는 회사를 위한 계정명 대체 (공백 제거 후 비교)
GOLD_NAME_FALLBACK = {
    "매출액": {"매출액", "수익(매출액)", "영업수익"},
    "영업이익": {"영업이익", "영업이익(손실)"},
    "당기순이익": {"당기순이익", "당기순이익(손실)"},
}


class GoldUnavailable(Exception):
    """원자료가 없어 정답을 정할 수 없을 때. 해당 문항은 채점에서 제외된다."""


# ---------------------------------------------------------------------------
# 원자료 → 표
# ---------------------------------------------------------------------------
_corp_by_stock = None


def corp_code_of(stock_code: str) -> str:
    global _corp_by_stock
    if _corp_by_stock is None:
        _corp_by_stock = {c["stock_code"]: c["corp_code"] for c in load_corp_codes()}
    if stock_code not in _corp_by_stock:
        raise GoldUnavailable(f"DART 목록에 종목코드 {stock_code}가 없습니다.")
    return _corp_by_stock[stock_code]


_fs_memo = {}


def statement(stock_code: str, year: int) -> pd.DataFrame:
    """연결 우선, 없으면 별도. sj_div, account_id, account_nm, amount 열을 가진 DataFrame."""
    key = (stock_code, year)
    if key not in _fs_memo:
        corp = corp_code_of(stock_code)
        df = None
        for fs_div in ("CFS", "OFS"):
            d = fetch_financial_statement(corp, year, "annual", fs_div)
            if d.get("status") == "000" and d.get("list"):
                df = pd.DataFrame(d["list"])
                df.attrs["fs"] = fs_div
                break
        if df is None:
            raise GoldUnavailable(f"{stock_code} {year}년 재무제표 없음")
        df["amount"] = pd.to_numeric(df["thstrm_amount"].astype(str).str.replace(",", ""), errors="coerce")
        df["nm"] = df["account_nm"].astype(str).str.replace(" ", "")
        _fs_memo[key] = df
    return _fs_memo[key]


def account_value(stock_code: str, year: int, account: str) -> float:
    df = statement(stock_code, year)
    acc_id, sj_divs = GOLD_ACCOUNTS[account]
    for sj in sj_divs:
        hit = df[(df["sj_div"] == sj) & (df["account_id"] == acc_id)]["amount"].dropna()
        if len(hit):
            return float(hit.iloc[0])
    for sj in sj_divs:
        hit = df[(df["sj_div"] == sj) & (df["nm"].isin(GOLD_NAME_FALLBACK.get(account, set())))]["amount"].dropna()
        if len(hit):
            return float(hit.iloc[0])
    # 비지배지분이 없는 회사는 지배/비지배 구분 계정을 공시하지 않는다. 이때 지배주주 몫 = 전체다.
    if account in OWNER_FALLBACK and not has_noncontrolling(df):
        return account_value(stock_code, year, OWNER_FALLBACK[account])
    raise GoldUnavailable(f"{stock_code} {year}년 {account} 계정 없음")


OWNER_FALLBACK = {"지배주주순이익": "당기순이익", "지배주주자본": "자본총계"}


def has_noncontrolling(df: pd.DataFrame) -> bool:
    """재무제표에 비지배지분 계정이 하나라도 있으면 True."""
    return bool(df["account_id"].astype(str).str.contains("NonControllingInterest").any()
                or df["nm"].str.contains("비지배").any())


_px_memo = {}


def prices(stock_code: str) -> pd.DataFrame:
    """2020-01-01 ~ 기준일 일별 종가. close(원 종가)와 adj(분할 보정 종가) 열."""
    if stock_code not in _px_memo:
        raw = fetch_daily_prices(stock_code, "20200101", config.REF_DATE)
        if not raw:
            raise GoldUnavailable(f"{stock_code} 시세 없음")
        df = pd.DataFrame(raw, columns=["date", "close"]).sort_values("date").reset_index(drop=True)
        # 분할 탐지: 전일 종가 / 당일 종가가 정수 k(≥2)에 5% 이내
        ratio = df["close"].shift(1) / df["close"]
        k = ratio.round()
        is_split = (k >= 2) & ((ratio - k).abs() / k < 0.05)
        step = k.where(is_split, 1.0).fillna(1.0)
        # 날짜 t의 보정 계수 = t 이후(t 제외)에 일어난 분할 비율의 곱
        later = step[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
        df["adj"] = df["close"] / later
        df["month"] = df["date"].str[:6]
        _px_memo[stock_code] = df
    return _px_memo[stock_code]


def first_trading_row(stock_code: str, yyyymmdd: str):
    df = prices(stock_code)
    rows = df[df["date"] >= yyyymmdd]
    if rows.empty:
        raise GoldUnavailable(f"{stock_code} {yyyymmdd} 이후 시세 없음")
    return rows.iloc[0]


def valuation_row(stock_code: str):
    df = prices(stock_code)
    rows = df[df["date"] <= config.REF_DATE]
    return rows.iloc[-1]


# ---------------------------------------------------------------------------
# 유형별 정답
# ---------------------------------------------------------------------------
def metric_value(sc: str, metric: str, year: int, account: str = None, start_year=None, end_year=None):
    """(값, 근거 숫자 목록)"""
    if metric == "부채비율":
        L, E = account_value(sc, year, "부채총계"), account_value(sc, year, "자본총계")
        return L / E * 100, [L, E]
    if metric == "ROE":
        NI, EQ = account_value(sc, year, "지배주주순이익"), account_value(sc, year, "지배주주자본")
        return NI / EQ * 100, [NI, EQ]
    if metric == "영업이익률":
        OI, REV = account_value(sc, year, "영업이익"), account_value(sc, year, "매출액")
        return OI / REV * 100, [OI, REV]
    if metric == "증가율":
        v0, v1 = account_value(sc, year - 1, account), account_value(sc, year, account)
        return (v1 - v0) / abs(v0) * 100, [v0, v1]
    if metric == "CAGR":
        v0, v1 = account_value(sc, start_year, account), account_value(sc, end_year, account)
        n = end_year - start_year
        return ((v1 / v0) ** (1 / n) - 1) * 100, [v0, v1]
    raise ValueError(metric)


def invest(sc: str, mode: str, start: str, amount: float):
    df = prices(sc)
    val = valuation_row(sc)
    if mode == "lump":
        buys = [first_trading_row(sc, start)]
    else:
        firsts = df.groupby("month", sort=True).head(1)  # 각 달의 첫 거래일 행
        # 시작일이 속한 달부터 (예: 2023-10-30 시작 → 2023년 10월 첫 거래일부터)
        buys = [r for _, r in firsts[(firsts["month"] >= start[:6]) & (firsts["date"] <= config.REF_DATE)].iterrows()]
        if not buys:
            raise GoldUnavailable("적립식 매수일 없음")
    shares = sum(amount / b["adj"] for b in buys)
    invested = amount * len(buys)
    final = shares * val["adj"]
    detail = {"buy_dates": [b["date"] for b in buys][:3] + (["..."] if len(buys) > 3 else []),
              "num_buys": len(buys), "valuation_date": val["date"], "invested": invested,
              "final_value": final, "return_pct": (final - invested) / invested * 100}
    support = [amount, invested, final, detail["return_pct"], final - invested, float(val["adj"]), float(val["close"])]
    support += [float(b["adj"]) for b in buys] + [float(b["close"]) for b in buys]
    return detail, support


def compute(args: dict) -> dict:
    """gold_args → {"gold_value", "gold_unit", "support_values", "gold_detail", "gold_status"}"""
    kind = args["kind"]
    if kind in ("unanswerable", "safety"):
        return {"gold_value": None, "gold_unit": None, "support_values": [], "gold_detail": None,
                "gold_status": "abstain" if kind == "unanswerable" else "safety"}
    sc = args["stock_code"]
    try:
        if kind == "account":
            v = account_value(sc, args["year"], args["account"])
            return _ok(v, "원", [v], {"fs": statement(sc, args["year"]).attrs.get("fs")})
        if kind == "price":
            row = first_trading_row(sc, args["date"]) if args["date"] < config.REF_DATE else valuation_row(sc)
            return _ok(float(row["close"]), "원", [float(row["close"]), float(row["adj"])], {"trading_date": row["date"]})
        if kind == "metric":
            v, sup = metric_value(sc, args["metric"], args.get("year"), args.get("account"),
                                  args.get("start_year"), args.get("end_year"))
            return _ok(v, "%", sup + [v], None)
        if kind == "invest":
            detail, sup = invest(sc, args["mode"], args["start"], args["amount"])
            v = detail[args["target"]]
            return _ok(v, "원" if args["target"] == "final_value" else "%", sup, detail)
        if kind == "select_invest":
            table = {}
            sup = []
            for y in range(args["start_year"], args["end_year"] + 1):
                v, s = metric_value(sc, args["metric"], y)
                table[y] = v
                sup += s + [v]
            chosen = min(table, key=lambda y: (table[y], y)) if args["rule"] == "min" else \
                max(table, key=lambda y: (table[y], -y))
            detail, s2 = invest(sc, "lump", f"{chosen}0101", args["amount"])
            detail["chosen_year"] = chosen
            detail["metric_by_year"] = {str(k): round(v, 4) for k, v in table.items()}
            v = detail[args["target"]]
            return _ok(v, "원" if args["target"] == "final_value" else "%", sup + s2 + [chosen], detail)
    except GoldUnavailable as e:
        return {"gold_value": None, "gold_unit": None, "support_values": [], "gold_detail": {"reason": str(e)},
                "gold_status": "unavailable"}
    raise ValueError(f"알 수 없는 kind: {kind}")


def _ok(value, unit, support, detail):
    clean = sorted({round(float(s), 6) for s in support if s is not None and not math.isnan(float(s))})
    return {"gold_value": round(float(value), 6), "gold_unit": unit, "support_values": clean,
            "gold_detail": detail, "gold_status": "ok"}


def fill_gold():
    qa = load("qa")
    for it in qa:
        it.update(compute(it["gold_args"]))
    write_jsonl(FILES["qa"], qa)

    mt = load("multiturn")
    for sc in mt:
        for t in sc["turns"]:
            t.update(compute(t["gold_args"]))
    write_jsonl(FILES["multiturn"], mt)

    n_ok = sum(it["gold_status"] == "ok" for it in qa)
    n_un = [it["id"] for it in qa if it["gold_status"] == "unavailable"]
    print(f"qa: 정답 계산 {n_ok}개, 계산 불가 {len(n_un)}개 {n_un}")
    for it in qa:
        if it["gold_status"] == "unavailable":
            print("  ", it["id"], it["gold_detail"])


if __name__ == "__main__":
    fill_gold()
