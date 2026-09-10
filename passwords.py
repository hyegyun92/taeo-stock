"""
비밀번호 해시.

평문은 어디에도 저장하지 않는다. 스프레드시트에 남는 것은 해시뿐이고,
해시에서 원래 비밀번호를 되돌릴 수는 없다.

bcrypt가 설치되어 있으면 bcrypt를, 없으면 표준 라이브러리의 PBKDF2를 쓴다.
두 형식 모두 읽을 수 있으므로, 나중에 bcrypt를 넣어도 기존 계정이 그대로 로그인된다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets as pysecrets
import string

try:
    import bcrypt as _bcrypt
except ImportError:  # 로컬에 없어도 PBKDF2로 돌아간다
    _bcrypt = None

PBKDF2_ROUNDS = 600_000  # OWASP 권고 수준
BCRYPT_ROUNDS = 12

MIN_LENGTH = 8


def hash_password(plain: str) -> str:
    """비밀번호를 저장 가능한 문자열로 바꾼다."""
    if not plain:
        raise ValueError("비밀번호가 비어 있습니다.")
    if _bcrypt is not None:
        return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt(BCRYPT_ROUNDS)).decode("ascii")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(plain: str, stored: str) -> bool:
    """
    입력한 비밀번호가 저장된 해시와 맞는지 본다.

    형식이 깨졌거나 값이 비어 있어도 예외를 던지지 않고 False를 돌려준다.
    로그인 화면에서 오류 내용이 새어 나가지 않게 하기 위해서다.
    """
    if not plain or not stored:
        return False
    stored = stored.strip()

    if stored.startswith("$2"):  # bcrypt
        if _bcrypt is None:
            return False
        try:
            return _bcrypt.checkpw(plain.encode("utf-8"), stored.encode("ascii"))
        except (ValueError, TypeError):
            return False

    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt_b64, digest_b64 = stored.split("$")
            expected = base64.b64decode(digest_b64)
            actual = hashlib.pbkdf2_hmac(
                "sha256", plain.encode("utf-8"), base64.b64decode(salt_b64), int(rounds)
            )
            return hmac.compare_digest(expected, actual)  # 시간차 공격 방지
        except (ValueError, TypeError):
            return False

    return False


def needs_rehash(stored: str) -> bool:
    """bcrypt를 쓸 수 있게 됐는데 아직 PBKDF2로 저장된 계정인가."""
    return _bcrypt is not None and not stored.strip().startswith("$2")


def check_strength(plain: str) -> str | None:
    """
    약한 비밀번호를 걸러낸다. 문제가 없으면 None.

    복잡한 규칙을 강요하기보다 길이를 우선한다. 짧고 복잡한 것보다
    길고 외우기 쉬운 쪽이 실제로 더 안전하고, 메모지에 적히지 않는다.
    """
    if len(plain) < MIN_LENGTH:
        return f"비밀번호는 {MIN_LENGTH}자 이상이어야 합니다."
    if plain.isdigit():
        return "숫자만으로는 안 됩니다. 글자를 섞어주세요."
    lowered = plain.lower()
    for weak in ("password", "12345678", "qwerty", "taeo", "admin", "1q2w3e4r"):
        if weak in lowered:
            return "너무 쉽게 짐작되는 비밀번호입니다."
    return None


def generate_password(length: int = 12) -> str:
    """비밀번호 초기화용 임시 비밀번호. 헷갈리는 글자는 뺀다."""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "Il1O0")
    return "".join(pysecrets.choice(alphabet) for _ in range(length))


LOGIN_ID_RE = re.compile(r"^[a-z0-9_]{3,20}$")


def check_login_id(login_id: str) -> str | None:
    if not LOGIN_ID_RE.match(login_id or ""):
        return "아이디는 영문 소문자, 숫자, 밑줄로 3~20자입니다."
    return None
