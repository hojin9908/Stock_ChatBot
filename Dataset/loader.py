"""Dataset/의 jsonl 파일 3개를 읽어 파이썬 리스트로 돌려준다."""
import json

import config

FILES = {"qa": "qa.jsonl", "multiturn": "multiturn.jsonl", "safety": "safety.jsonl"}


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def load(set_name: str, split: str = "all") -> list:
    """set_name: qa | multiturn | safety,  split: dev | test | all (safety는 split 구분 없음)."""
    rows = read_jsonl(config.DATASET_DIR / FILES[set_name])
    if split != "all" and set_name != "safety":
        rows = [r for r in rows if r.get("split") == split]
    return rows


def load_qa(split="all"):
    return load("qa", split)


def load_multiturn(split="all"):
    return load("multiturn", split)


def load_safety():
    return load("safety")
