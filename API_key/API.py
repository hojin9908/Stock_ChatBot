"""프로젝트 최상위의 .env 파일에서 API 키 3개를 읽어 다른 모듈에 넘겨준다.

코드에 키를 직접 적지 않기 위한 중간 단계다. .env의 [Your API Key] 자리에 각자의 키를 넣는다.

    OPENAI_API_KEY=sk-...
    DART_API_KEY=(DART 인증키 40자리)
    DATA_GO_KR_KEY=(공공데이터포털 일반 인증키, Decoding)

DATA_GO_KR_KEY는 공공데이터포털의 "일반 인증키(Decoding)"를 넣는다.
requests가 URL 인코딩을 자동으로 하므로 Encoding 키를 넣으면 이중 인코딩되어 인증에 실패한다.
"""
import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_ENV_NAMES = {
    "openai": "OPENAI_API_KEY",
    "dart": "DART_API_KEY",
    "data_go_kr": "DATA_GO_KR_KEY",
}
PLACEHOLDER = "[Your API Key]"
_loaded = False


def _load_env_file() -> None:
    """.env를 읽어 환경변수로 등록한다. 이미 설정된 환경변수는 덮어쓰지 않는다."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not _ENV_PATH.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH, override=False)
        return
    except ImportError:
        pass
    # python-dotenv가 없을 때의 단순 파서
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def get_key(name: str) -> str:
    """name은 "openai", "dart", "data_go_kr" 중 하나. 키가 없으면 어떤 키가 없는지 알려준다."""
    _load_env_file()
    env_name = _ENV_NAMES[name]
    value = os.getenv(env_name, "").strip()
    if not value or value == PLACEHOLDER or (value.startswith("[") and value.endswith("]")):
        raise RuntimeError(
            f"{env_name}가 설정되지 않았습니다. 프로젝트 최상위의 .env 파일에서 "
            f"'{env_name}={PLACEHOLDER}'의 {PLACEHOLDER} 부분(대괄호 포함)을 지우고 발급받은 키를 넣으세요."
        )
    return value


def get_keys() -> dict:
    """키 3개를 한 번에 돌려준다. 하나라도 없으면 오류."""
    return {name: get_key(name) for name in _ENV_NAMES}
