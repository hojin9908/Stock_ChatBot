# Windows의 Git Bash / MSYS2 터미널용 설치 스크립트.
#
# 사용법 (프로젝트 폴더에서):   source setup_windows.sh
#
# 하는 일
#   1. MSYS2에 딸린 파이썬이 아닌 "일반 Windows 파이썬"(python.org, Anaconda)을 찾는다.
#   2. 그 파이썬으로 프로젝트 전용 가상환경 .venv를 만든다 (잘못 만들어진 .venv가 있으면 다시 만든다).
#   3. 패키지를 설치하고 오프라인 테스트를 실행한다.
#   4. 가상환경을 켠 상태로 끝난다. 이후 이 창에서 `python`은 .venv의 파이썬이다.
#
# 터미널을 새로 열었을 때는 프로젝트 폴더에서 `source .venv/Scripts/activate`만 하면 된다.
# (source로 실행하므로 오류가 나도 터미널이 닫히지 않게 exit 대신 return을 쓴다.)

_hw2_to_unix() {
    if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s\n' "$1"; fi
}

_hw2_find_python() {
    local lad home c v
    lad="$(_hw2_to_unix "${LOCALAPPDATA:-}")"
    home="$(_hw2_to_unix "${USERPROFILE:-}")"
    # 선호 버전 순서대로 python.org 설치 위치를 찾는다
    for v in 312 311 313 310; do
        for c in "$lad/Programs/Python/Python$v/python.exe" "/c/Program Files/Python$v/python.exe"; do
            if [ -f "$c" ]; then printf '%s\n' "$c"; return 0; fi
        done
    done
    # Python 런처(py.exe)가 있으면 그것이 아는 3.x 경로를 물어본다
    for c in "/c/Windows/py.exe" "$lad/Programs/Python/Launcher/py.exe"; do
        if [ -f "$c" ]; then
            local p
            p="$("$c" -3 -c "import sys; print(sys.executable)" 2>/dev/null | tr -d '\r')"
            if [ -n "$p" ]; then _hw2_to_unix "$p"; return 0; fi
        fi
    done
    # Anaconda / Miniconda
    for c in "$home/anaconda3/python.exe" "$home/miniconda3/python.exe" "/c/ProgramData/anaconda3/python.exe"; do
        if [ -f "$c" ]; then printf '%s\n' "$c"; return 0; fi
    done
    return 1
}

_hw2_setup() {
    local here py vpy
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$here" || return 1

    echo "[1/4] 일반 Windows 파이썬 찾는 중..."
    if ! py="$(_hw2_find_python)"; then
        echo
        echo "  일반 Windows 파이썬을 찾지 못했습니다. 아래 둘 중 하나로 설치한 뒤 터미널을 새로 열고 다시 실행하세요."
        echo "    (a) 이 창에서:  winget.exe install -e --id Python.Python.3.12"
        echo "    (b) https://www.python.org/downloads/ 에서 3.12 설치 (첫 화면 'Add python.exe to PATH' 체크)"
        return 1
    fi
    if ! "$py" -c "import sys; assert sys.version_info >= (3, 9)" 2>/dev/null; then
        echo "  찾은 파이썬($py)이 3.9 미만입니다. 3.12를 설치하세요: winget.exe install -e --id Python.Python.3.12"
        return 1
    fi
    echo "  사용할 파이썬: $py ($("$py" --version 2>&1 | tr -d '\r'))"

    echo "[2/4] 가상환경 .venv 준비 중..."
    vpy=".venv/Scripts/python.exe"
    if [ -d .venv ] && [ ! -f "$vpy" ]; then
        echo "  MSYS2 파이썬으로 만들어진 .venv가 있어 지우고 다시 만듭니다."
        rm -rf .venv
    fi
    if [ ! -f "$vpy" ]; then
        "$py" -m venv .venv || { echo "  가상환경 생성 실패"; return 1; }
    fi
    echo "  가상환경 파이썬: $("$vpy" -c "import sys; print(sys.executable)" | tr -d '\r')"

    echo "[3/4] 패키지 설치 중 (1~2분)..."
    "$vpy" -m pip install --quiet --upgrade pip || { echo "  pip 업그레이드 실패"; return 1; }
    "$vpy" -m pip install --quiet -r requirements.txt || { echo "  패키지 설치 실패: 위 오류를 보내주세요"; return 1; }
    "$vpy" -c "import openai, requests, pandas, dotenv; print('  설치 확인: openai', openai.__version__, '/ pandas', pandas.__version__)"

    echo "[4/4] 오프라인 테스트 (API·키 불필요)..."
    "$vpy" -m tests.test_offline | tail -1

    # 이 창의 python이 가상환경 파이썬을 가리키게 한다
    # shellcheck disable=SC1091
    source .venv/Scripts/activate 2>/dev/null
    if [ "$(command -v python)" != "$(pwd)/.venv/Scripts/python" ] && [ "$(command -v python)" != "$(pwd)/.venv/Scripts/python.exe" ]; then
        alias python="$(pwd)/.venv/Scripts/python.exe"
    fi
    echo
    echo "완료. 지금 이 창에서 python은: $(command -v python 2>/dev/null || alias python)"
    echo "다음: .env의 [Your API Key] 세 곳을 채운 뒤"
    echo "  git update-index --skip-worktree .env"
    echo "  python -m Dataset.gold"
}

_hw2_setup
