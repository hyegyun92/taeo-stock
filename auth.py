"""
로그인, 권한, 세션.

권한은 역할(ADMIN / STAFF)로 시작하지만, 사용자마다 권한을 더하거나 뺄 수 있게
해 두었다. 나중에 "직원 A는 입고는 되지만 매입단가는 못 본다" 같은 요구가
생겨도 구조를 갈아엎지 않아도 된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .passwords import hash_password, verify_password
from .clock import now_kst

# ---------------------------------------------------------------------
# 권한
# ---------------------------------------------------------------------
PERMISSIONS: dict[str, str] = {
    "dashboard.view": "대시보드 보기",
    "stock.view": "재고 조회",
    "stock.adjust": "재고 조정·폐기·파손",
    "price.view": "매입단가와 재고금액 보기",
    "inbound.create": "입고 등록",
    "sales.view": "판매 조회",
    "sales.import": "판매 파일 반영",
    "order.view": "발주 관리",
    "product.create": "상품 등록",
    "product.edit": "상품정보 수정",
    "mapping.edit": "판매처 상품명 연결",
    "user.manage": "사용자 관리",
    "audit.view": "작업 기록 조회",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "ADMIN": set(PERMISSIONS),
    "STAFF": {
        "dashboard.view",
        "stock.view",
        "inbound.create",
        "sales.view",
        "order.view",
        "mapping.edit",
    },
}

ROLE_LABEL = {"ADMIN": "관리자", "STAFF": "일반 사용자"}

# 로그인 보안
MAX_FAILURES = 5           # 이 횟수만큼 틀리면
FAILURE_WINDOW_MIN = 15    # 이 시간 안에서 세고
LOCK_MINUTES = 10          # 이만큼 잠근다
DEFAULT_SESSION_HOURS = 8


@dataclass
class User:
    user_id: str
    login_id: str
    user_name: str
    role: str
    is_active: bool = True
    extra_permissions: set[str] = field(default_factory=set)
    denied_permissions: set[str] = field(default_factory=set)

    @property
    def role_label(self) -> str:
        return ROLE_LABEL.get(self.role, self.role)

    def permissions(self) -> set[str]:
        base = set(ROLE_PERMISSIONS.get(self.role, set()))
        return (base | self.extra_permissions) - self.denied_permissions

    def can(self, permission: str) -> bool:
        return permission in self.permissions()


def _split(value: str) -> set[str]:
    return {p.strip() for p in str(value or "").split(",") if p.strip()}


def row_to_user(row: dict) -> User:
    return User(
        user_id=str(row.get("user_id", "")),
        login_id=str(row.get("login_id", "")),
        user_name=str(row.get("user_name", "")),
        role=str(row.get("role", "STAFF")).upper(),
        is_active=str(row.get("is_active", "TRUE")).upper() != "FALSE",
        extra_permissions=_split(row.get("extra_permissions")),
        denied_permissions=_split(row.get("denied_permissions")),
    )


# ---------------------------------------------------------------------
# 로그인 판정
# ---------------------------------------------------------------------
class AuthResult:
    """로그인 결과. 실패 사유는 화면에 그대로 내보내지 않는다."""

    def __init__(self, user: User | None, reason: str = "", locked_until: datetime | None = None):
        self.user = user
        self.reason = reason
        self.locked_until = locked_until

    @property
    def ok(self) -> bool:
        return self.user is not None


def count_recent_failures(login_records: list[dict], login_id: str, now: datetime) -> int:
    """
    최근 실패 횟수. 마지막으로 성공한 뒤부터 다시 센다.

    목록을 정렬해 위에서부터 훑는 방식은 쓰지 않는다. 같은 초에 기록된
    성공과 실패가 섞이면 어느 쪽이 먼저인지가 정렬 방식에 따라 달라지고,
    그러면 잠길 사람이 안 잠기거나 그 반대가 된다.
    마지막 성공 시각을 먼저 정하고, 그보다 뒤의 실패만 센다.
    """
    since = now - timedelta(minutes=FAILURE_WINDOW_MIN)
    mine = []
    for row in login_records:
        if str(row.get("login_id", "")).lower() != login_id.lower():
            continue
        when = _parse(row.get("occurred_at"))
        if when is None or when < since:
            continue
        mine.append((when, str(row.get("result", ""))))

    successes = [when for when, result in mine if result == "SUCCESS"]
    last_success = max(successes) if successes else None

    return sum(
        1 for when, result in mine
        if result != "SUCCESS" and (last_success is None or when > last_success)
    )


def _parse(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def authenticate(user_rows: list[dict], login_records: list[dict],
                 login_id: str, password: str, now: datetime | None = None) -> AuthResult:
    """
    아이디와 비밀번호를 확인한다.

    아이디가 없을 때도 비밀번호를 한 번 대조하는 시늉을 한다.
    응답 시간 차이로 "이 아이디는 존재한다"는 사실이 새어 나가지 않게 하기 위해서다.
    """
    now = now or now_kst()
    login_id = (login_id or "").strip().lower()

    failures = count_recent_failures(login_records, login_id, now)
    if failures >= MAX_FAILURES:
        return AuthResult(None, "LOCKED", now + timedelta(minutes=LOCK_MINUTES))

    row = next((r for r in user_rows if str(r.get("login_id", "")).lower() == login_id), None)

    if row is None:
        # 존재하지 않는 아이디여도 같은 시간을 쓴다
        verify_password(password or "x", "pbkdf2_sha256$600000$AAAA$AAAA")
        return AuthResult(None, "BAD_CREDENTIALS")

    if not verify_password(password, str(row.get("password_hash", ""))):
        return AuthResult(None, "BAD_CREDENTIALS")

    if str(row.get("is_active", "TRUE")).upper() == "FALSE":
        return AuthResult(None, "INACTIVE")

    return AuthResult(row_to_user(row))


def failure_message(result: AuthResult) -> str:
    """
    화면에 보여줄 말.

    아이디가 틀렸는지 비밀번호가 틀렸는지 구분해 주지 않는다.
    구분해 주면 어떤 아이디가 존재하는지 알려주는 셈이 된다.
    비활성 계정도 같은 문구를 쓴다.
    """
    if result.reason == "LOCKED":
        return (f"로그인 시도가 너무 많았습니다. {LOCK_MINUTES}분 뒤에 다시 시도하세요. "
                "본인이 아니라면 관리자에게 알려주세요.")
    return "아이디 또는 비밀번호가 올바르지 않습니다."


# ---------------------------------------------------------------------
# 세션
# ---------------------------------------------------------------------
def session_expiry(hours: int = DEFAULT_SESSION_HOURS, now: datetime | None = None) -> datetime:
    return (now or now_kst()) + timedelta(hours=hours)


def session_alive(expires_at, now: datetime | None = None) -> bool:
    when = _parse(expires_at)
    if when is None:
        return False
    return (now or now_kst()) < when


# ---------------------------------------------------------------------
# 계정 만들기
# ---------------------------------------------------------------------
def next_user_id(user_rows: list[dict]) -> str:
    highest = 0
    for row in user_rows:
        value = str(row.get("user_id", ""))
        if value.startswith("U"):
            try:
                highest = max(highest, int(value[1:]))
            except ValueError:
                pass
    return f"U{highest + 1:04d}"


def build_user_row(user_id: str, login_id: str, user_name: str, role: str,
                   password: str, now: datetime | None = None) -> dict:
    stamp = (now or now_kst()).isoformat(timespec="seconds")
    return {
        "user_id": user_id,
        "login_id": login_id.strip().lower(),
        "user_name": user_name.strip(),
        "password_hash": hash_password(password),
        "role": role.upper(),
        "is_active": "TRUE",
        "extra_permissions": "",
        "denied_permissions": "",
        "created_at": stamp,
        "last_login_at": "",
        "password_changed_at": stamp,
        "must_change_password": "FALSE",
    }
