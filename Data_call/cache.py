"""외부 API 응답을 파일로 저장해 두고, 같은 요청이 오면 저장된 응답을 돌려준다.

목적
1. 재현성: 실험을 몇 번 다시 돌려도 모든 구성이 같은 원자료를 본다.
2. 호출 한도 절약: DART와 공공데이터포털은 키마다 하루 호출 수가 제한된다.

캐시 파일 이름은 "요청 주소 + 파라미터(키 제외)"의 해시다.
API 키는 파일 이름에도, 파일 내용에도 남기지 않는다.
"""
import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Optional

import requests

import config

SECRET_PARAMS = {"crtfc_key", "serviceKey"}


class CacheMiss(RuntimeError):
    """CACHE_ONLY 모드에서 캐시에 없는 요청이 들어왔을 때."""


class ApiResponseError(RuntimeError):
    """API가 정상 응답 형식이 아닌 것을 돌려줬을 때 (키 오류, 한도 초과 등)."""


def _cache_path(namespace: str, url: str, params: dict, ext: str) -> Path:
    visible = sorted((k, str(v)) for k, v in params.items() if k not in SECRET_PARAMS)
    digest = hashlib.sha256(json.dumps([url, visible], ensure_ascii=False).encode()).hexdigest()[:24]
    return config.CACHE_DIR / namespace / f"{digest}.{ext}"


def cached_get(
    url: str,
    params: dict,
    *,
    namespace: str,
    kind: str = "json",
    validate: Optional[Callable] = None,
    timeout: int = 30,
    retries: int = 3,
):
    """GET 요청을 캐시와 함께 수행한다.

    kind="json"이면 파싱된 JSON(dict)을, kind="bin"이면 bytes를 돌려준다.
    validate(data) -> bool 이 False면 캐시에 저장하지 않고 ApiResponseError를 낸다
    (오류 응답이 캐시에 박혀 계속 재사용되는 것을 막기 위함).
    """
    ext = "json" if kind == "json" else "bin"
    path = _cache_path(namespace, url, params, ext)
    if path.exists():
        if kind == "json":
            return json.loads(path.read_text(encoding="utf-8"))
        return path.read_bytes()

    if config.CACHE_ONLY:
        raise CacheMiss(f"캐시에 없는 요청입니다 (CACHE_ONLY 모드): {url} {[(k, v) for k, v in params.items() if k not in SECRET_PARAMS]}")

    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            if kind == "json":
                try:
                    data = resp.json()
                except ValueError:
                    # 공공데이터포털은 키 오류일 때 resultType=json이어도 XML을 돌려준다
                    raise ApiResponseError(f"JSON이 아닌 응답: {resp.text[:300]}")
            else:
                data = resp.content
            if validate is not None and not validate(data):
                snippet = json.dumps(data, ensure_ascii=False)[:300] if kind == "json" else data[:200]
                raise ApiResponseError(f"정상 응답이 아닙니다: {snippet}")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            if kind == "json":
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            else:
                tmp.write_bytes(data)
            tmp.replace(path)
            return data
        except ApiResponseError:
            raise  # 키 오류·한도 초과는 재시도해도 같다
        except requests.RequestException as e:
            last_error = e
            time.sleep(1.5 * (attempt + 1))
    raise ApiResponseError(f"요청 실패 ({retries}회 시도): {last_error}")
