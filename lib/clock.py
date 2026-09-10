"""
시간. 한국 시간을 기준으로 삼는다.

이 작은 모듈을 따로 둔 이유가 있다. 여기 있던 함수가 sheets.py 안에 있으면
인증이나 기록 로직이 gspread를 끌고 오게 되고, 그러면 구글 계정 없이는
로그인 코드를 시험해볼 수 없다. 검증할 수 없는 인증 코드는 배포하면 안 된다.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()
