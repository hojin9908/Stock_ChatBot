"""DART(금융감독원 전자공시) OpenAPI에서 원본 데이터를 받아온다.

이 파일은 계산이나 가공을 하지 않는다. 응답을 그대로 돌려주는 것이 역할이다.

사용하는 API
- corpCode.xml : 전체 회사 고유번호 목록(ZIP 안의 XML). 처음 한 번만 받는다.
- fnlttSinglAcntAll.json : 단일회사 전체 재무제표.
"""
import io
import zipfile
import xml.etree.ElementTree as ET
from functools import lru_cache

from API_key.API import get_key
from Data_call.cache import ApiResponseError, cached_get

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FS_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

# 보고서 코드: 사업보고서(연간), 반기, 1분기, 3분기
REPORT_CODES = {"annual": "11011", "half": "11012", "q1": "11013", "q3": "11014"}

# DART 응답 status
#   000 정상, 013 조회된 데이터 없음, 010 등록되지 않은 키, 020 요청 제한 초과
DART_OK = {"000", "013"}


class DartError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(f"DART status {status}: {message}")
        self.status = status


@lru_cache(maxsize=1)
def load_corp_codes() -> tuple:
    """상장사 목록을 돌려준다.

    반환: ({"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930"}, ...)
    stock_code가 비어 있는 비상장사는 제외한다.
    """
    data = cached_get(
        CORP_CODE_URL,
        {"crtfc_key": get_key("dart")},
        namespace="dart_corpcode",
        kind="bin",
        validate=lambda b: b[:2] == b"PK",  # ZIP 파일은 "PK"로 시작한다. 아니면 오류 응답이다.
        timeout=60,
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = ET.fromstring(zf.read(zf.namelist()[0]))
    corps = []
    for item in root.iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        if not stock:
            continue
        corps.append({
            "corp_code": (item.findtext("corp_code") or "").strip(),
            "corp_name": (item.findtext("corp_name") or "").strip(),
            "stock_code": stock,
        })
    return tuple(corps)


def fetch_financial_statement(corp_code: str, year: int, report: str = "annual", fs_div: str = "CFS") -> dict:
    """회사·연도별 재무제표 전체 계정을 DART 응답 그대로 돌려준다.

    fs_div: "CFS" 연결재무제표, "OFS" 별도(개별)재무제표
    반환: {"status": "000", "message": ..., "list": [{"sj_div": "BS", "account_id": ..., "account_nm": ..., "thstrm_amount": ...}, ...]}
          데이터가 없으면 status "013"과 빈 list.
    """
    params = {
        "crtfc_key": get_key("dart"),
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": REPORT_CODES[report],
        "fs_div": fs_div,
    }
    try:
        data = cached_get(
            FS_URL, params, namespace="dart_fs",
            validate=lambda d: isinstance(d, dict) and d.get("status") in DART_OK,
        )
    except ApiResponseError as e:
        raise DartError("?", str(e)) from e
    data.setdefault("list", [])
    return data
