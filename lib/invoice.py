"""
읽은 거래명세표를 입고 후보로 바꾼다.

두 가지를 지킨다.

  하나. 확신이 서지 않는 줄은 절대 자동으로 입고하지 않는다. '확인필요'로 남긴다.
  둘.  사람이 한 번 확정한 매칭은 기억한다. 같은 거래처는 늘 같은 상품명을 쓰므로,
       두 번째 명세표부터는 손댈 일이 줄어든다.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from .vision import InvoiceRead

# 자동매칭으로 인정하는 이름 유사도. 이보다 낮으면 사람에게 묻는다.
AUTO_THRESHOLD = 0.75
# 후보로 보여줄 최소 유사도
SUGGEST_THRESHOLD = 0.35

UNMATCHED = "— 확인필요 —"


# 도매 명세표는 축약이 심하다. 실제 명세표에서 본 표기들:
#   해)재래식된장 3kg/4   →  해찬들, 3kg짜리 4입 한 박스
#   금표)튀김가루 1K/10   →  금표, 1kg짜리 10입
#   청)장아찌소스 1.7k/8  →  청정원, 1.7kg짜리 8입
# 이걸 모르면 낱말이 안 맞아 멀쩡한 상품이 전부 확인필요로 떨어진다.
MAKER_ABBREV = re.compile(r"^([가-힣A-Za-z]{1,4})\s*\)")
UNIT_K = re.compile(r"(\d)k(?!g)")


def _norm(text) -> str:
    cleaned = re.sub(r"[\s_\-()\[\]/,.]", "", str(text or "")).lower()
    # 14k, 1.7k 처럼 kg를 k 한 글자로 줄여 쓴다
    return UNIT_K.sub(r"\1kg", cleaned)


def split_abbrev(raw_name: str) -> tuple[str, str]:
    """'해)재래식된장 3kg/4' → ('해', '재래식된장 3kg/4')"""
    match = MAKER_ABBREV.match(str(raw_name or "").strip())
    if not match:
        return "", str(raw_name or "").strip()
    return match.group(1), str(raw_name)[match.end():].strip()


def main_spec(text) -> str:
    """'3kg/4' → '3kg'. 슬래시 뒤는 한 박스에 몇 개인지라 규격이 아니다."""
    return _norm(str(text or "").split("/")[0])


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s()\[\]/,]+", str(text or "")) if len(_norm(t)) > 1]


@dataclass
class DraftLine:
    """확정 전의 한 줄. 사용자가 화면에서 고칠 수 있는 상태."""
    raw_name: str
    qty: int                   # 음수면 반품·상계로 돌려보낸 것
    product_code: str = ""
    basis: str = ""            # 무엇을 근거로 붙였는지
    confidence: str = "high"   # 사진에서 읽은 확신도
    unit_price: float | None = None
    box_qty: int | None = None
    item_code: str = ""        # 그 거래처가 명세표에 찍은 코드
    candidates: list[tuple[str, float]] = field(default_factory=list)

    @property
    def per_box(self) -> int | None:
        """명세표에서 읽어낸 입수. 박스와 총수량이 다 있을 때만 안다."""
        if not self.box_qty or self.qty % self.box_qty != 0:
            return None
        return abs(self.qty // self.box_qty)

    @property
    def is_return(self) -> bool:
        return self.qty < 0

    @property
    def reason(self) -> str:
        """
        원장에 남길 사유.

        받은 것과 돌려보낸 것을 같은 사유로 적으면, 나중에 이력을 봐도
        무슨 일이 있었는지 알 수 없다.
        """
        return "RETURN_OUT" if self.is_return else "PURCHASE"

    @property
    def status(self) -> str:
        if not self.product_code:
            return "확인필요"
        mark = "반품" if self.is_return else "입고"
        if self.basis == "사용자 확정":
            return f"{mark}·직접"
        return f"{mark}·자동"

    @property
    def ready(self) -> bool:
        # 0은 아무 일도 없었다는 뜻이라 내보내지 않는다. 음수는 반품이므로 내보낸다.
        return bool(self.product_code) and self.qty != 0


@dataclass
class Draft:
    """확정 전의 거래명세표 전체."""
    vendor_code: str = ""
    vendor_name: str = ""
    invoice_date: str = ""
    invoice_no: str = ""
    invoice_no_generated: bool = False
    lines: list[DraftLine] = field(default_factory=list)
    total_amount: float | None = None
    note: str = ""

    @property
    def ready_lines(self) -> list[DraftLine]:
        return [line for line in self.lines if line.ready]

    @property
    def pending_count(self) -> int:
        return sum(1 for line in self.lines if not line.ready)

    @property
    def inbound_lines(self) -> list[DraftLine]:
        return [line for line in self.ready_lines if not line.is_return]

    @property
    def return_lines(self) -> list[DraftLine]:
        return [line for line in self.ready_lines if line.is_return]

    @property
    def is_return(self) -> bool:
        """전부 반품인 명세표."""
        return bool(self.ready_lines) and not self.inbound_lines

    @property
    def mixed(self) -> bool:
        """받은 것과 돌려보낸 것이 한 장에 섞여 있다."""
        return bool(self.inbound_lines) and bool(self.return_lines)

    def shortfall(self, stock_of) -> list[tuple[DraftLine, int]]:
        """
        돌려보낼 수량이 지금 재고보다 많은 줄.

        같은 상품이 여러 줄에 나올 수 있으므로 합쳐서 본다.
        재고가 음수로 떨어지면 판매처에 올려둔 재고와 어긋나 품절 사고가 난다.
        """
        needed: dict[str, int] = {}
        for line in self.return_lines:
            needed[line.product_code] = needed.get(line.product_code, 0) + (-line.qty)

        problems = []
        for line in self.return_lines:
            code = line.product_code
            if code not in needed:
                continue
            have = stock_of(code)
            if needed[code] > have:
                problems.append((line, have))
                del needed[code]
        return problems


# ---------------------------------------------------------------------
# 매칭
# ---------------------------------------------------------------------
def similarity(invoice_name: str, product: dict) -> float:
    """
    명세표 이름과 상품 마스터를 견준다.

    낱말이 얼마나 겹치는지를 보되, 도매 명세표의 축약을 감안한다.
    제조사 약칭은 따로 떼어 견주고, 규격은 슬래시 앞부분만 본다.

    규격이 어긋나면 크게 깎는다. 설탕 15kg와 3kg를 같은 것으로 보면
    재고가 통째로 틀어지기 때문이다.
    """
    abbrev, rest = split_abbrev(invoice_name)
    haystack = _norm(rest)
    if not haystack:
        return 0.0

    words = _tokens(product.get("product_name"))
    if not words:
        return 0.0
    hits = sum(1 for w in words if _norm(w) in haystack)
    score = hits / len(words)

    # 제조사 약칭이 상품명이나 제조사의 앞머리와 맞으면 근거가 하나 더 있는 셈
    if abbrev:
        head = _norm(abbrev)
        target = _norm(f"{product.get('product_name', '')}{product.get('maker', '')}")
        if head and (target.startswith(head) or f"{head}" in _norm(product.get("maker"))[:len(head)]):
            score = min(1.0, score + 0.15)

    spec = main_spec(product.get("spec"))
    if spec:
        if spec in haystack:
            score = min(1.0, score + 0.25)
        else:
            # 명세표에 다른 규격이 적혀 있다면 다른 상품일 가능성이 높다
            other = re.search(r"\d+(?:\.\d+)?(?:kg|g|l|ml|매|롤|입|개)", haystack)
            if other:
                score *= 0.4
    return round(min(score, 1.0), 3)


def build_matcher(products: list[dict], aliases: list[dict], mappings: list[dict]):
    """
    명세표 상품명을 내부 상품코드로 바꾸는 함수.

    순서가 곧 신뢰도다.
      1. 거래처 코드 — 그 거래처가 명세표에 찍는 코드. 한 번 확정하면 가장 확실하다
      2. 거래처 별칭 — 사람이 전에 확정한 상품명. 다음부터 손댈 일이 없어진다
      3. 판매처 이름 — 쿠팡·네이버에 올린 이름과 겹치는 경우
      4. 이름 유사도 — 위 셋이 없을 때만. 기준을 넘어야 자동매칭으로 본다

    명세표에 찍힌 코드를 전역 바코드로 보지 않는다. 같은 물건이라도 거래처마다
    다른 코드를 쓰고, 그 코드는 그쪽 사정으로 바뀌기도 한다. 우리 기준은
    내부 SKU 하나뿐이고, 거래처 코드는 거래처와 묶어서 기억한다.
    """
    active = [p for p in products if str(p.get("is_active", "TRUE")).upper() != "FALSE"]

    by_vendor_code: dict[tuple[str, str], str] = {}
    by_alias: dict[tuple[str, str], str] = {}
    for a in aliases:
        vendor = str(a.get("vendor_code", ""))
        product = str(a.get("product_code", ""))
        code = _norm(a.get("vendor_item_code"))
        if code:
            by_vendor_code[(vendor, code)] = product
        name = _norm(a.get("vendor_item_name"))
        if name:
            by_alias[(vendor, name)] = product

    channel_names: list[tuple[str, str]] = []
    for m in mappings:
        key = _norm(f"{m.get('channel_name', '')}{m.get('option_name', '')}")
        if key:
            channel_names.append((key, str(m.get("product_code", ""))))
    channel_names.sort(key=lambda x: -len(x[0]))

    codes = {p["product_code"] for p in active}

    def match(vendor_code: str, name: str, item_code: str = "") -> tuple[str, str, list[tuple[str, float]]]:
        """(상품코드, 근거, 후보목록). 못 찾으면 코드가 빈 문자열."""
        haystack = _norm(name)

        clean_code = _norm(item_code)
        if clean_code:
            hit = by_vendor_code.get((vendor_code, clean_code))
            if hit and hit in codes:
                return hit, "거래처 코드", []

        alias_hit = by_alias.get((vendor_code, haystack))
        if alias_hit and alias_hit in codes:
            return alias_hit, "이전 확정", []

        for key, code in channel_names:
            if code in codes and (key in haystack or haystack in key):
                return code, "판매처 이름", []

        scored = sorted(
            ((p["product_code"], similarity(name, p)) for p in active),
            key=lambda x: -x[1],
        )
        candidates = [(c, s) for c, s in scored[:5] if s >= SUGGEST_THRESHOLD]

        if scored and scored[0][1] >= AUTO_THRESHOLD:
            # 1등과 2등이 비슷하면 사람이 골라야 한다
            runner_up = scored[1][1] if len(scored) > 1 else 0.0
            if scored[0][1] - runner_up >= 0.15:
                return scored[0][0], f"이름 유사 {scored[0][1]:.0%}", candidates
        return "", "", candidates

    return match


# ---------------------------------------------------------------------
# 명세표 번호
# ---------------------------------------------------------------------
def content_fingerprint(vendor_code: str, invoice_date: str, items) -> str:
    """
    사진 내용으로 만드는 지문.

    같은 명세표를 다시 찍어도 같은 값이 나온다. 번호가 없는 명세표에서
    중복을 잡는 근거가 된다. 수량까지 넣는 이유는, 같은 거래처에서 같은 날
    다른 물건을 두 번 받는 일이 실제로 있기 때문이다.
    """
    # 수량의 부호까지 넣는다. 같은 물건을 받은 명세표와 돌려보낸 명세표는
    # 품목과 개수가 같아도 전혀 다른 거래다.
    parts = sorted(f"{_norm(getattr(i, 'name', ''))}x{getattr(i, 'qty', 0):+d}" for i in items)
    seed = f"{vendor_code}|{invoice_date}|{'|'.join(parts)}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]


def auto_invoice_no(vendor_code: str, invoice_date: str, items) -> str:
    """번호가 없는 명세표에 붙일 임시 번호. 같은 내용이면 같은 번호가 나온다."""
    stamp = (invoice_date or str(date.today())).replace("-", "")
    return f"IN-{stamp}-{content_fingerprint(vendor_code, invoice_date, items).upper()}"


def guess_vendor(vendor_name: str, vendors: list[dict]) -> str:
    """명세표에 적힌 상호를 등록된 매입처와 견준다. 못 찾으면 빈 문자열."""
    target = _norm(vendor_name)
    if not target:
        return ""
    for v in vendors:
        if _norm(v.get("vendor_name")) == target:
            return str(v.get("vendor_code", ""))
    for v in vendors:
        name = _norm(v.get("vendor_name"))
        if name and (name in target or target in name):
            return str(v.get("vendor_code", ""))
    return ""


# ---------------------------------------------------------------------
# 초안 만들기
# ---------------------------------------------------------------------
def build_draft(read: InvoiceRead, products: list[dict], vendors: list[dict],
                aliases: list[dict], mappings: list[dict],
                fallback_date: date | None = None) -> Draft:
    """읽은 내용을 화면에서 고칠 수 있는 초안으로 바꾼다."""
    vendor_code = guess_vendor(read.vendor_name, vendors)
    matcher = build_matcher(products, aliases, mappings)

    lines = []
    for item in read.items:
        code, basis, candidates = matcher(vendor_code, item.full_name, item.barcode)
        # 명세표에 찍힌 코드는 그 거래처의 코드로만 쓴다. 전역 바코드가 아니다.
        # 사진에서 흐릿하게 읽은 줄은 자동매칭을 믿지 않는다
        if item.confidence == "low" and basis.startswith("이름 유사"):
            code, basis = "", ""
        lines.append(DraftLine(
            raw_name=item.full_name, qty=item.qty, product_code=code, basis=basis,
            confidence=item.confidence, unit_price=item.unit_price,
            box_qty=item.box_qty, item_code=item.barcode, candidates=candidates,
        ))

    invoice_date = read.invoice_date or (fallback_date or date.today()).isoformat()
    invoice_no = read.invoice_no
    generated = False
    if not invoice_no:
        invoice_no = auto_invoice_no(vendor_code, invoice_date, read.items)
        generated = True

    return Draft(
        vendor_code=vendor_code,
        vendor_name=read.vendor_name,
        invoice_date=invoice_date,
        invoice_no=invoice_no,
        invoice_no_generated=generated,
        lines=lines,
        total_amount=read.total_amount,
        note=read.note,
    )


def already_processed(draft: Draft, existing_keys: set[str]) -> bool:
    """이 명세표가 이미 입고됐는지. 한 줄이라도 들어가 있으면 처리된 것으로 본다."""
    from .inventory import purchase_key
    return any(
        purchase_key(draft.vendor_code, draft.invoice_no, i + 1, line.product_code) in existing_keys
        for i, line in enumerate(draft.lines) if line.product_code
    )


def to_entries(draft: Draft) -> list[dict]:
    """
    원장에 넣을 줄로 바꾼다. 확인이 끝난 것만 나간다.

    음수 줄은 부호를 그대로 두고 사유만 반품출고로 바꾼다. 재고는 원장 합산이므로
    음수가 들어가면 그만큼 줄어든다. 부호를 뒤집는 코드는 어디에도 없다.
    """
    from .inventory import purchase_key
    entries = []
    for i, line in enumerate(draft.lines):
        if not line.ready:
            continue
        entries.append({
            "txn_key": purchase_key(draft.vendor_code, draft.invoice_no, i + 1, line.product_code),
            "product_code": line.product_code,
            "qty_change": line.qty,
            "reason": line.reason,
            "party": draft.vendor_name or draft.vendor_code,
            "ref_no": draft.invoice_no,
            "occurred_on": draft.invoice_date,
        })
    return entries


def new_aliases(draft: Draft, known: list[dict]) -> list[dict]:
    """
    이번에 사람이 확정한 매칭 중 아직 기억하지 않은 것.

    상품명과 함께 그 거래처가 쓰는 코드도 기억한다. 코드는 이름보다 안 바뀌므로
    다음 명세표부터는 이름이 조금 달라져도 정확히 붙는다.
    """
    seen = {(str(a.get("vendor_code", "")), _norm(a.get("vendor_item_name"))) for a in known}
    fresh, added = [], set()
    for line in draft.lines:
        if not line.ready or not draft.vendor_code:
            continue
        key = (draft.vendor_code, _norm(line.raw_name))
        if not key[1] or key in seen or key in added:
            continue
        added.add(key)
        fresh.append({
            "vendor_code": draft.vendor_code,
            "vendor_item_name": line.raw_name,
            "vendor_item_code": line.item_code,
            "product_code": line.product_code,
        })
    return fresh
