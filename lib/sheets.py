"""
구글 스프레드시트를 데이터베이스로 쓴다.

주의할 점이 하나 있다. 스프레드시트에는 UNIQUE 제약이 없다.
Postgres였다면 DB가 튕겨냈을 중복 입력을 여기서 코드가 막아야 한다.
그래서 원장에 줄을 넣는 통로를 append_ledger 하나로 좁혀 두었다.
다른 곳에서 worksheet.append_row 를 직접 부르지 말 것.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

KST = ZoneInfo("Asia/Seoul")

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
        "vendor_code", "vendor_item_name", "product_code", "confirmed_at",
    ],
    "unmatched": [
        "created_at", "channel", "order_no", "raw_name", "qty",
        "occurred_on", "resolved_code",
    ],
}

LEDGER_TXN_KEY_COL = 1  # stock_ledger 탭에서 txn_key가 몇 번째 열인가


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


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
