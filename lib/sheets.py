"""
구글 스프레드시트를 데이터베이스로 쓴다.

주의할 점이 하나 있다. 스프레드시트에는 UNIQUE 제약이 없다.
Postgres였다면 DB가 튕겨냈을 중복 입력을 여기서 코드가 막아야 한다.
그래서 원장에 줄을 넣는 통로를 append_ledger 하나로 좁혀 두었다.
다른 곳에서 worksheet.append_row 를 직접 부르지 말 것.
"""
from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials

from .clock import now_kst, today_kst  # noqa: F401  (기존 import 경로 유지)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# 탭 이름과 열 순서. 열을 늘릴 때는 반드시 뒤에 붙인다.
# 중간에 끼워 넣으면 기존 데이터가 한 칸씩 밀린다.
SCHEMA: dict[str, list[str]] = {
    "product": [
        "product_code", "product_name", "spec", "maker", "vendor_code",
        "barcode", "unit_label", "order_unit", "purchase_price",
        "safety_stock", "is_active", "created_at",
        # units_per_box: 한 박스에 몇 개 들었는지. 1이면 박스 표시를 하지 않는다.
        # item_kind: SALE(판매용) 또는 OFFSET(상계용).
        #   상계용은 거래처와 오가는 물건이라 온라인 판매 기록이 없다.
        #   섞어 두면 발주 계산과 재고금액이 오염된다.
        "units_per_box", "item_kind",
    ],
    "vendor": [
        "vendor_code", "vendor_name", "lead_days", "cycle_days",
        "min_amount", "contact", "memo",
    ],
    "channel_mapping": [
        "product_code", "channel", "channel_product_id", "channel_option_id",
        "channel_name", "option_name", "created_at",
    ],
    "stock_ledger": [
        "txn_key", "product_code", "qty_change", "reason", "party",
        "ref_no", "occurred_on", "created_by", "created_at",
    ],
    "vendor_alias": [
        # vendor_item_code: 그 거래처가 명세표에 찍는 코드. 우리 SKU와는 별개다.
        # 같은 물건이라도 거래처마다 코드가 다르므로 거래처와 묶어서 기억한다.
        "vendor_code", "vendor_item_name", "vendor_item_code",
        "product_code", "confirmed_at",
    ],
    "unmatched": [
        "created_at", "channel", "order_no", "raw_name", "qty",
        "occurred_on", "resolved_code",
    ],
    # ----- 인증과 기록 -----
    "app_user": [
        "user_id", "login_id", "user_name", "password_hash", "role",
        "is_active", "extra_permissions", "denied_permissions",
        "created_at", "last_login_at", "password_changed_at", "must_change_password",
    ],
    "audit_log": [
        "occurred_at", "user_id", "login_id", "user_name", "action", "feature",
        "product_code", "before_value", "after_value", "qty_change",
        "order_no", "ref_no", "note",
    ],
    "login_log": [
        "occurred_at", "login_id", "result", "user_name", "client", "note",
    ],
    # 거래명세표 사진 처리 기록. 원본 사진을 어디에 뒀는지도 여기 적는다.
    "invoice_log": [
        "created_at", "vendor_code", "vendor_name", "invoice_date", "invoice_no",
        "invoice_no_generated", "note_kind", "line_count", "total_qty", "total_amount",
        "image_ref", "created_by", "note",
    ],
}

# 사람이 시트에서 직접 손대면 안 되는 탭. 앱은 여기에 줄을 더하기만 한다.
APPEND_ONLY_TABS = {"stock_ledger", "audit_log", "login_log"}

LEDGER_TXN_KEY_COL = 1  # stock_ledger 탭에서 txn_key가 몇 번째 열인가


class Store:
    """스프레드시트 한 개를 감싼다."""

    def __init__(self, service_account_info: dict, spreadsheet_key: str):
        creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        self.client = gspread.authorize(creds)
        self.book = self.client.open_by_key(spreadsheet_key)
        self._tabs: dict[str, gspread.Worksheet] = {}

    # ---------- 기본 ----------
    def tab(self, name: str) -> gspread.Worksheet:
        if name not in self._tabs:
            try:
                self._tabs[name] = self.book.worksheet(name)
            except gspread.WorksheetNotFound as exc:
                raise RuntimeError(
                    f"'{name}' 탭이 없습니다. scripts/bootstrap_sheet.py 를 먼저 실행하세요."
                ) from exc
        return self._tabs[name]

    def read(self, name: str) -> list[dict]:
        records = self.tab(name).get_all_records(expected_headers=SCHEMA[name])
        return [r for r in records if any(str(v).strip() for v in r.values())]

    def read_all(self) -> dict[str, list[dict]]:
        """화면 한 번 그리는 데 필요한 것을 모두 읽는다."""
        return {name: self.read(name) for name in
                ("product", "vendor", "channel_mapping", "stock_ledger")}

    # ---------- 사용자 ----------
    def read_users(self) -> list[dict]:
        """
        사용자 목록은 read_all 과 따로 읽는다.

        비밀번호 해시가 들어 있어서, 화면을 그릴 때마다 통째로 캐시에 올려두고 싶지 않다.
        필요한 순간에만 읽는다.
        """
        return self.read("app_user")

    def add_user(self, row: dict) -> None:
        self._append("app_user", [row])

    def update_user(self, user_id: str, changes: dict) -> None:
        worksheet = self.tab("app_user")
        ids = worksheet.col_values(1)
        try:
            row_no = ids.index(user_id) + 1
        except ValueError as exc:
            raise RuntimeError(f"사용자 {user_id} 를 찾지 못했습니다.") from exc

        header = SCHEMA["app_user"]
        updates = []
        for field, value in changes.items():
            if field not in header:
                continue
            col_no = header.index(field) + 1
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row_no, col_no),
                "values": [[str(value)]],
            })
        if updates:
            worksheet.batch_update(updates, value_input_option="USER_ENTERED")

    # ---------- 기록 ----------
    def append_audit(self, rows: list[dict]) -> int:
        """
        작업 기록에 줄을 더한다.

        이 클래스에는 감사 로그를 고치거나 지우는 함수가 없다.
        일부러 만들지 않았다. 지울 수 있는 기록은 기록이 아니다.
        """
        return self._append("audit_log", rows)

    def read_audit(self) -> list[dict]:
        return self.read("audit_log")

    def append_login(self, login_id: str, result: str, user_name: str = "",
                     client: str = "", note: str = "") -> None:
        self._append("login_log", [{
            "occurred_at": now_kst().isoformat(timespec="seconds"),
            "login_id": login_id, "result": result, "user_name": user_name,
            "client": client, "note": note,
        }])

    def read_login_log(self, limit: int = 400) -> list[dict]:
        rows = self.read("login_log")
        return rows[-limit:]

    def _append(self, name: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        header = SCHEMA[name]
        values = [[str(r.get(col, "")) for col in header] for r in rows]
        self.tab(name).append_rows(values, value_input_option="USER_ENTERED")
        return len(values)

    # ---------- 원장 ----------
    def existing_txn_keys(self) -> set[str]:
        """
        원장의 txn_key 열만 읽는다.

        전체를 읽지 않는 이유는 속도다. 원장이 3만 줄이 되어도
        열 하나만 가져오면 1초 안에 끝난다.
        """
        column = self.tab("stock_ledger").col_values(LEDGER_TXN_KEY_COL)
        return {value for value in column[1:] if value}

    def append_ledger(self, entries: list[dict], created_by: str = "사장님") -> dict:
        """
        원장에 줄을 추가한다. 이미 있는 txn_key는 조용히 건너뛴다.

        같은 판매 파일을 열 번 올려도 재고는 한 번만 움직인다.
        돌려주는 값: {"written": 반영된 수, "skipped": 중복으로 건너뛴 수}

        주의: 스프레드시트에는 원자적 제약이 없으므로, 두 사람이 같은 순간에
        같은 파일을 올리면 둘 다 통과할 수 있다. 혼자 쓰는 동안은 문제되지 않지만
        여러 명이 쓰기 시작하면 Postgres로 옮기는 게 맞다.
        """
        if not entries:
            return {"written": 0, "skipped": 0}

        existing = self.existing_txn_keys()
        stamp = now_kst().isoformat(timespec="seconds")
        fresh, seen, skipped = [], set(), 0

        for entry in entries:
            key = entry.get("txn_key") or ""
            if key and (key in existing or key in seen):
                skipped += 1
                continue
            if key:
                seen.add(key)
            qty = int(entry["qty_change"])
            if qty == 0:
                continue
            fresh.append({
                "txn_key": key,
                "product_code": entry["product_code"],
                "qty_change": qty,
                "reason": entry["reason"],
                "party": entry.get("party", ""),
                "ref_no": entry.get("ref_no", ""),
                "occurred_on": entry.get("occurred_on") or today_kst().isoformat(),
                "created_by": entry.get("created_by", created_by),
                "created_at": stamp,
            })

        written = self._append("stock_ledger", fresh)
        return {"written": written, "skipped": skipped}

    # ---------- 마스터 ----------
    def next_product_code(self, products: list[dict]) -> str:
        highest = 0
        for p in products:
            code = str(p.get("product_code", ""))
            if code.startswith("TAEO-"):
                try:
                    highest = max(highest, int(code.split("-")[1]))
                except (ValueError, IndexError):
                    pass
        return f"TAEO-{highest + 1:05d}"

    def add_product(self, product: dict) -> None:
        product.setdefault("is_active", "TRUE")
        product.setdefault("created_at", now_kst().isoformat(timespec="seconds"))
        self._append("product", [product])

    def update_product(self, product_code: str, changes: dict) -> None:
        """상품 마스터의 몇 칸을 고친다. 재고 관련 열은 애초에 없다."""
        worksheet = self.tab("product")
        codes = worksheet.col_values(1)
        try:
            row_no = codes.index(product_code) + 1
        except ValueError as exc:
            raise RuntimeError(f"{product_code} 를 찾지 못했습니다.") from exc

        header = SCHEMA["product"]
        updates = []
        for field, value in changes.items():
            if field not in header:
                continue
            col_no = header.index(field) + 1
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row_no, col_no),
                "values": [[str(value)]],
            })
        if updates:
            worksheet.batch_update(updates, value_input_option="USER_ENTERED")

    def add_vendor(self, vendor: dict) -> None:
        self._append("vendor", [vendor])

    def add_mapping(self, mapping: dict) -> None:
        mapping.setdefault("created_at", now_kst().isoformat(timespec="seconds"))
        self._append("channel_mapping", [mapping])

    def read_aliases(self) -> list[dict]:
        """거래처별 상품명 별칭. 한 번 확정한 매칭을 다음에 다시 쓴다."""
        return self.read("vendor_alias")

    def add_aliases(self, rows: list[dict]) -> int:
        stamp = now_kst().isoformat(timespec="seconds")
        return self._append("vendor_alias",
                            [{**r, "confirmed_at": stamp} for r in rows])

    def read_invoice_log(self) -> list[dict]:
        return self.read("invoice_log")

    def append_invoice_log(self, row: dict) -> None:
        self._append("invoice_log", [{
            **row, "created_at": now_kst().isoformat(timespec="seconds"),
        }])

    def add_alias(self, vendor_code: str, item_name: str, product_code: str) -> None:
        self._append("vendor_alias", [{
            "vendor_code": vendor_code,
            "vendor_item_name": item_name,
            "product_code": product_code,
            "confirmed_at": now_kst().isoformat(timespec="seconds"),
        }])

    def log_unmatched(self, rows: list[dict]) -> int:
        stamp = now_kst().isoformat(timespec="seconds")
        return self._append("unmatched", [{**r, "created_at": stamp} for r in rows])
