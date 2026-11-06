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


def load(set_name: str) -> list:
    """set_name: qa | multiturn | safety. 항상 전체 문항을 돌려준다."""
    return read_jsonl(config.DATASET_DIR / FILES[set_name])


def load_qa():
    return load("qa")


def load_multiturn():
    return load("multiturn")


def load_safety():
    return load("safety")
