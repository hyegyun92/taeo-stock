"""
작업 기록.

원장(stock_ledger)과 역할을 나눈다.

  원장이 맡는 것 — 재고를 움직인 모든 일. 누가·언제·무엇을·얼마나가 이미 다 들어 있다.
  여기가 맡는 것 — 원장이 담지 못하는 것.
      · 조정 전후의 값 (원장에는 증감만 남는다)
      · 상품정보 수정 (재고와 무관하지만 추적이 필요하다)
      · 사용자 관리
      · 대량 반영 요약

같은 사실을 두 곳에 적지 않는다. 두 기록이 어긋나면 어느 쪽이 맞는지
알 수 없게 되고, 그러면 추적한다는 목적 자체가 무너진다.
작업 기록 화면에서는 둘을 합쳐서 보여준다.
"""
from __future__ import annotations

from datetime import datetime

ACTIONS = {
    "INBOUND": "입고등록",
    "STOCK_ADJUST": "재고조정",
    "DISPOSE": "폐기처리",
    "DAMAGE": "파손처리",
    "RETURN": "반품입고",
    "SAMPLE": "샘플출고",
    "SALE_IMPORT": "판매반영",
    "SALE_MANUAL": "판매등록",
    "CANCEL": "판매취소",
    "PRODUCT_CREATE": "상품등록",
    "PRODUCT_EDIT": "상품수정",
    "MAPPING_ADD": "판매처연결",
    "USER_CREATE": "사용자생성",
    "USER_EDIT": "사용자수정",
    "USER_RESET_PW": "비밀번호초기화",
    "USER_DISABLE": "계정비활성화",
    "USER_ENABLE": "계정활성화",
    "PASSWORD_CHANGE": "비밀번호변경",
}


def entry(user, action: str, *, feature: str = "", product_code: str = "",
          before: str | int | float = "", after: str | int | float = "",
          qty_change: str | int = "", order_no: str = "", ref_no: str = "",
          note: str = "", now: datetime | None = None) -> dict:
    """감사 로그 한 줄을 만든다. 실제 기록은 Store.append_audit 가 한다."""
    from .clock import now_kst
    stamp = (now or now_kst()).isoformat(timespec="seconds")
    return {
        "occurred_at": stamp,
        "user_id": getattr(user, "user_id", ""),
        "login_id": getattr(user, "login_id", ""),
        "user_name": getattr(user, "user_name", ""),
        "action": action,
        "feature": feature or ACTIONS.get(action, action),
        "product_code": product_code,
        "before_value": "" if before == "" else str(before),
        "after_value": "" if after == "" else str(after),
        "qty_change": "" if qty_change == "" else str(qty_change),
        "order_no": order_no,
        "ref_no": ref_no,
        "note": note,
    }


def merge_view(audit_rows: list[dict], ledger_rows: list[dict],
               product_names: dict[str, str], reason_labels: dict[str, str]) -> list[dict]:
    """
    감사 로그와 원장을 하나의 표로 합친다.

    사장님이 "누가 뭘 했나"를 볼 때는 둘을 나눠 볼 이유가 없다.
    """
    merged = []

    for row in audit_rows:
        code = str(row.get("product_code", ""))
        merged.append({
            "일시": str(row.get("occurred_at", "")).replace("T", " ")[:16],
            "사용자": row.get("user_name", "") or row.get("login_id", ""),
            "작업": ACTIONS.get(str(row.get("action", "")), row.get("feature", "")),
            "대상": product_names.get(code, code) if code else row.get("note", ""),
            "변경 전": row.get("before_value", ""),
            "변경 후": row.get("after_value", ""),
            "증감": row.get("qty_change", ""),
            "번호": row.get("ref_no", "") or row.get("order_no", ""),
            "비고": row.get("note", "") if code else "",
            "_source": "작업기록",
            "_code": code,
            "_user": row.get("login_id", ""),
            "_at": str(row.get("occurred_at", "")),
        })

    for row in ledger_rows:
        code = str(row.get("product_code", ""))
        qty = row.get("qty_change", 0)
        merged.append({
            "일시": str(row.get("created_at", "") or row.get("occurred_on", "")).replace("T", " ")[:16],
            "사용자": row.get("created_by", ""),
            "작업": reason_labels.get(str(row.get("reason", "")), row.get("reason", "")),
            "대상": product_names.get(code, code),
            "변경 전": "",
            "변경 후": "",
            "증감": f"{int(qty):+d}" if str(qty).lstrip("-").isdigit() else qty,
            "번호": row.get("ref_no", ""),
            "비고": row.get("party", ""),
            "_source": "재고원장",
            "_code": code,
            "_user": row.get("created_by", ""),
            "_at": str(row.get("created_at", "") or row.get("occurred_on", "")),
        })

    merged.sort(key=lambda r: r["_at"], reverse=True)
    return merged
