"""기능별 효과(주효과)와 Tool 1 × Tool 2 상호작용 (계획서 4.2절).

주효과: 나머지 두 기능의 조합마다 "켰을 때 − 껐을 때"를 구해 평균한다.
         8개 구성이 다 없으면 있는 쌍만 평균하고, 몇 쌍을 썼는지 함께 돌려준다.
상호작용 I12 = [Acc(T1 on, T2 on) − Acc(T1 on, T2 off)] − [Acc(T1 off, T2 on) − Acc(T1 off, T2 off)]
          (Tool 3 on/off 각각에서 구해 평균)
"""
import itertools

import config


def _by_flags(values: dict) -> dict:
    """{"C0": 20.0, ...} → {(False, False, False): 20.0, ...}"""
    return {config.CONFIGS[c]: v for c, v in values.items() if c in config.CONFIGS and v is not None}


def main_effect(values: dict, tool_index: int) -> dict:
    v = _by_flags(values)
    diffs = []
    for others in itertools.product([False, True], repeat=2):
        on, off = list(others), list(others)
        on.insert(tool_index, True)
        off.insert(tool_index, False)
        on, off = tuple(on), tuple(off)
        if on in v and off in v:
            diffs.append(v[on] - v[off])
    return {"effect": round(sum(diffs) / len(diffs), 2) if diffs else None, "pairs": len(diffs)}


def interaction_12(values: dict) -> dict:
    v = _by_flags(values)
    terms = []
    for t3 in (False, True):
        keys = [(True, True, t3), (True, False, t3), (False, True, t3), (False, False, t3)]
        if all(k in v for k in keys):
            terms.append((v[keys[0]] - v[keys[1]]) - (v[keys[2]] - v[keys[3]]))
    return {"effect": round(sum(terms) / len(terms), 2) if terms else None, "pairs": len(terms)}


def effects_table(metric_by_config: dict) -> list:
    """metric_by_config: {"A-D Acc@1%": {"C0": .., "C1": ..}, ...} → 표의 행 목록."""
    rows = []
    for metric, values in metric_by_config.items():
        row = {"지표": metric}
        for name, idx in (("Tool 1 효과", 0), ("Tool 2 효과", 1), ("Tool 3 효과", 2)):
            e = main_effect(values, idx)
            row[name] = e["effect"]
            row[f"{name} 쌍 수"] = e["pairs"]
        i = interaction_12(values)
        row["Tool1×Tool2 상호작용"] = i["effect"]
        rows.append(row)
    return rows
