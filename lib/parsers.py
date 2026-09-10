"""
쿠팡 · 네이버 판매 파일을 읽어 원장에 넣을 줄로 바꾼다.

두 곳 모두 열 이름을 예고 없이 바꾼다. 그래서 열 순서를 고정하지 않고
이름으로 찾되, 못 찾으면 사용자가 직접 고르게 한다.
"""
from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd

from .inventory import sale_key

# 열 이름 후보. 위에 있을수록 우선.
COLUMN_HINTS = {
    "order": ["상품주문번호", "주문번호", "order_id", "orderid"],
    "name": ["상품명", "노출상품명", "등록상품명", "상품이름", "product_name"],
    "option": ["옵션명", "옵션정보", "옵션", "구매옵션", "option"],
    "qty": ["수량", "구매수량", "주문수량", "판매수량", "quantity", "qty"],
    "status": ["주문상태", "배송상태", "상태", "클레임상태", "주문세부상태"],
    "date": ["주문일시", "주문일", "결제일", "결제일시", "발주확인일"],
}

# 이 낱말이 상태 열에 있으면 판매로 치지 않는다
CANCEL_WORDS = ("취소", "반품", "환불", "미결제", "결제실패", "교환")


def _norm(text) -> str:
    return re.sub(r"[\s_\-()\[\]]", "", str(text)).lower()


def guess_column(columns: list[str], key: str) -> str | None:
    """열 이름으로 찾는다. 정확히 같은 것 먼저, 없으면 포함하는 것."""
    hints = [_norm(h) for h in COLUMN_HINTS[key]]
    normalized = {col: _norm(col) for col in columns}
    for hint in hints:
        for col, norm in normalized.items():
            if norm == hint:
                return col
    for hint in hints:
        for col, norm in normalized.items():
            if hint in norm:
                return col
    return None


# 머리글 후보를 판정할 때 쓰는 낱말 모음
_ALL_HINTS = {_norm(h) for hints in COLUMN_HINTS.values() for h in hints}


def _score_header(cells: list[str]) -> int:
    """이 줄이 머리글일 가능성. 아는 열 이름이 몇 개나 정확히 들어 있는가."""
    return sum(1 for cell in cells if _norm(cell) in _ALL_HINTS)


def read_table(uploaded) -> pd.DataFrame:
    """
    업로드된 파일을 표로 읽는다.

    쿠팡·네이버 다운로드 파일은 머리글 위에 안내 문구가 몇 줄 붙어 나온다.
    그대로 읽으면 pandas가 첫 줄을 기준으로 열 개수를 정해버려서, 정작 진짜
    머리글 줄을 불량 줄로 보고 버린다. 그래서 텍스트일 때는 pandas에 넘기기 전에
    줄을 직접 훑어 머리글 위치를 찾는다.
    """
    name = getattr(uploaded, "name", "").lower()
    raw = uploaded.read() if hasattr(uploaded, "read") else uploaded

    if name.endswith((".xlsx", ".xls")):
        probe = pd.read_excel(io.BytesIO(raw), header=None, nrows=15, dtype=str)
        best_row, best_hits = 0, 0
        for idx in range(len(probe)):
            cells = [str(c) for c in probe.iloc[idx].tolist() if pd.notna(c)]
            hits = _score_header(cells)
            if hits > best_hits:
                best_row, best_hits = idx, hits
        return pd.read_excel(io.BytesIO(raw), header=best_row, dtype=str)

    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else str(raw)
    return parse_text(text)


def _find_header_line(lines: list[str], sep: str) -> int:
    """아는 열 이름이 가장 많이 든 줄을 머리글로 본다. 없으면 첫 줄."""
    best_row, best_hits = 0, 0
    for idx, line in enumerate(lines[:15]):
        hits = _score_header(line.split(sep))
        if hits > best_hits:
            best_row, best_hits = idx, hits
    return best_row


def parse_text(text: str) -> pd.DataFrame:
    """엑셀에서 복사해 붙여넣은 내용이나 CSV 본문을 표로."""
    lines = [ln for ln in text.replace("\r", "").split("\n")]
    sep = "\t" if text.count("\t") >= text.count(",") else ","
    start = _find_header_line(lines, sep)
    body = "\n".join(lines[start:]).strip()
    if not body:
        raise ValueError("읽을 내용이 없습니다.")
    return pd.read_csv(io.StringIO(body), sep=sep, dtype=str,
                       on_bad_lines="skip", engine="python", skip_blank_lines=True)


def build_matcher(products: list[dict], mappings: list[dict]):
    """
    판매처 상품명을 내부 상품코드로 바꾸는 함수를 만든다.

    1순위는 등록된 매핑이다. 한 번 연결해 두면 그 뒤로는 확실하게 잡힌다.
    2순위는 상품명 낱말 겹침이다. 어디까지나 후보 제안이고,
    확정은 사람이 화면에서 눈으로 보고 한다.
    """
    by_channel: dict[str, list[tuple[str, str]]] = {}
    for m in mappings:
        key = _norm(f"{m.get('channel_name', '')}{m.get('option_name', '')}")
        if not key:
            continue
        by_channel.setdefault(m.get("channel", ""), []).append((key, m["product_code"]))
    # 긴 이름부터 맞춰야 '설탕 15kg'이 '설탕 3kg'보다 먼저 걸린다
    for rows in by_channel.values():
        rows.sort(key=lambda x: -len(x[0]))

    id_index: dict[tuple[str, str], str] = {}
    for m in mappings:
        for field in ("channel_product_id", "channel_option_id"):
            value = str(m.get(field, "") or "").strip()
            if value:
                id_index[(m.get("channel", ""), value)] = m["product_code"]

    tokens: list[tuple[str, list[str]]] = []
    for p in products:
        words = re.split(r"\s+", f"{p.get('product_name', '')} {p.get('spec', '')}".strip())
        words = [_norm(w) for w in words if len(_norm(w)) > 1]
        if words:
            tokens.append((p["product_code"], words))

    def match(channel: str, name: str, option: str = "", channel_id: str = "") -> tuple[str | None, str]:
        """(상품코드, 근거) 를 돌려준다. 못 찾으면 (None, '')."""
        cid = str(channel_id or "").strip()
        if cid and (channel, cid) in id_index:
            return id_index[(channel, cid)], "상품번호"

        hay = _norm(f"{name}{option}")
        if not hay:
            return None, ""
        for key, code in by_channel.get(channel, []):
            if key in hay or hay in key:
                return code, "등록된 이름"

        best_code, best_score = None, 0.0
        for code, words in tokens:
            hit = sum(1 for w in words if w in hay) / len(words)
            if hit > best_score:
                best_code, best_score = code, hit
        if best_score >= 0.6:
            return best_code, f"이름 유사 {best_score:.0%}"
        return None, ""

    return match


def parse_sales(df: pd.DataFrame, channel: str, columns: dict, matcher,
                existing_keys: set[str], default_date: date) -> pd.DataFrame:
    """
    판매 표를 원장 후보로 바꾼다.

    돌려주는 표의 열:
      txn_key, order_no, raw_name, qty, product_code, matched_name, why, status
    status 는 '반영' / '중복' / '매칭실패' / '취소건'
    """
    out = []
    for _, row in df.iterrows():
        def cell(key: str) -> str:
            col = columns.get(key)
            if not col or col not in df.columns:
                return ""
            value = row[col]
            return "" if pd.isna(value) else str(value).strip()

        qty_text = re.sub(r"[^\d\-]", "", cell("qty"))
        if not qty_text:
            continue
        qty = int(qty_text)
        if qty <= 0:
            continue

        name, option = cell("name"), cell("option")
        if not (name or option):
            continue

        order_no = cell("order") or "NOORDER"
        status = cell("status")
        occurred = cell("date")[:10] or default_date.isoformat()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", occurred):
            occurred = default_date.isoformat()

        code, why = matcher(channel, name, option)
        key = sale_key(channel, order_no, code or _norm(name + option)[:40])

        if any(word in status for word in CANCEL_WORDS):
            state = "취소건"
        elif key in existing_keys:
            state = "중복"
        elif code is None:
            state = "매칭실패"
        else:
            state = "반영"

        out.append({
            "txn_key": key,
            "order_no": order_no,
            "raw_name": f"{name} / {option}".strip(" /"),
            "qty": qty,
            "product_code": code or "",
            "why": why,
            "occurred_on": occurred,
            "status": state,
        })
    return pd.DataFrame(out)


def parse_invoice_text(text: str) -> pd.DataFrame:
    """
    거래명세표 사진을 Claude에게 읽혀 받은 표를 붙여넣을 때 쓴다.

    기대하는 열: 상품명, 수량 (규격·단가는 있으면 쓰고 없으면 넘어간다)
    """
    df = parse_text(text)
    columns = {key: guess_column(list(df.columns), key) for key in ("name", "qty", "option")}
    if not columns["qty"] or not columns["name"]:
        raise ValueError("상품명과 수량 열을 찾지 못했습니다. 머리글 줄까지 함께 붙여넣었는지 확인하세요.")
    rows = []
    for _, row in df.iterrows():
        qty_text = re.sub(r"[^\d\-]", "", str(row[columns["qty"]]))
        if not qty_text or int(qty_text) <= 0:
            continue
        rows.append({"raw_name": str(row[columns["name"]]).strip(), "qty": int(qty_text)})
    return pd.DataFrame(rows)
