"""
판매 구성표.

쿠팡·네이버에 올린 상품 하나가 실제 재고 여러 개로 나뉜다.

    "얼큰닭개장 + 맑은닭곰탕 2종세트"  →  얼큰닭개장 ×1, 맑은닭곰탕 ×1

단품도 같은 구조로 다룬다. 구성품이 하나뿐인 구성표일 뿐이다.
단품과 세트를 다른 길로 처리하면 나중에 반드시 한쪽만 고치는 실수가 난다.

가장 중요한 규칙: **한 번 사람이 확정한 구성표는 다시 추론하지 않는다.**
AI가 매번 판단하면 같은 상품이 어제와 오늘 다르게 분해되어,
재고가 어디서부터 틀어졌는지 알 수 없게 된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def _norm(text) -> str:
    return re.sub(r"[\s_\-()\[\]/,.+]", "", str(text or "")).lower()


def _int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError, AttributeError):
        return default


@dataclass
class Component:
    product_code: str
    qty: int = 1

    def __post_init__(self):
        self.qty = max(1, _int(self.qty, 1))


@dataclass
class Bom:
    """판매 SKU 하나의 구성표."""
    channel: str
    channel_name: str = ""
    option_name: str = ""
    channel_product_id: str = ""
    channel_option_id: str = ""
    components: list[Component] = field(default_factory=list)
    source: str = ""          # 무엇으로 찾았는지
    legacy: bool = False      # 예전 channel_mapping 에서 끌어온 것

    @property
    def is_single(self) -> bool:
        return len(self.components) == 1 and self.components[0].qty == 1

    @property
    def label(self) -> str:
        return " + ".join(f"{c.product_code}×{c.qty}" for c in self.components)

    def expand(self, order_qty: int) -> list[tuple[str, int]]:
        """
        주문수량을 실제 차감수량으로 바꾼다.

            "A+B 3세트" 2개 주문  →  A 6개, B 6개

        같은 상품이 구성표에 두 번 들어 있으면 합친다. 한 주문에서 같은 상품이
        두 줄로 나오면 고유키가 겹쳐 한 줄이 통째로 버려지기 때문이다.
        """
        merged: dict[str, int] = {}
        for c in self.components:
            merged[c.product_code] = merged.get(c.product_code, 0) + c.qty * order_qty
        return [(code, qty) for code, qty in merged.items() if qty]


# ---------------------------------------------------------------------
# 저장된 구성표 찾기
# ---------------------------------------------------------------------
def _id_key(channel: str, product_id: str, option_id: str) -> tuple[str, str, str]:
    return (channel, str(product_id or "").strip(), str(option_id or "").strip())


def _name_key(channel: str, name: str, option: str) -> tuple[str, str]:
    return (channel, _norm(f"{name}{option}"))


def build_index(bom_rows: list[dict], legacy_mappings: list[dict] | None = None):
    """
    구성표를 찾는 함수를 만든다.

    찾는 순서가 곧 신뢰도다.
      1. 상품번호 + 옵션ID  — 같은 상품이라도 옵션마다 구성이 다르므로 이게 가장 정확하다
      2. 상품번호만        — 옵션이 없는 상품
      3. 상품명 + 옵션명    — ID를 모를 때
      4. 예전 channel_mapping — 단품으로 연결해 둔 것. 구성품 하나짜리로 본다

    4번이 있어야 이미 쓰고 계신 단품 연결이 그대로 살아난다.
    """
    by_id: dict[tuple, Bom] = {}
    by_product: dict[tuple, Bom] = {}
    by_name: dict[tuple, Bom] = {}

    grouped: dict[tuple, Bom] = {}
    for row in bom_rows:
        if str(row.get("is_active", "TRUE")).upper() == "FALSE":
            continue
        code = str(row.get("component_product_code", "")).strip()
        if not code:
            continue
        channel = str(row.get("channel", "")).strip().upper()
        key = (
            channel,
            str(row.get("channel_product_id", "")).strip(),
            str(row.get("channel_option_id", "")).strip(),
            _norm(row.get("channel_name")),
            _norm(row.get("option_name")),
        )
        bom = grouped.get(key)
        if bom is None:
            bom = Bom(
                channel=channel,
                channel_name=str(row.get("channel_name", "")),
                option_name=str(row.get("option_name", "")),
                channel_product_id=str(row.get("channel_product_id", "")).strip(),
                channel_option_id=str(row.get("channel_option_id", "")).strip(),
            )
            grouped[key] = bom
        bom.components.append(Component(code, row.get("component_qty", 1)))

    for bom in grouped.values():
        if bom.channel_product_id:
            by_id[_id_key(bom.channel, bom.channel_product_id, bom.channel_option_id)] = bom
            if bom.channel_option_id:
                by_product.setdefault(
                    _id_key(bom.channel, bom.channel_product_id, ""), bom)
            else:
                by_product[_id_key(bom.channel, bom.channel_product_id, "")] = bom
        name_key = _name_key(bom.channel, bom.channel_name, bom.option_name)
        if name_key[1]:
            by_name[name_key] = bom

    # 예전 단품 연결을 구성품 하나짜리 구성표로 본다
    for row in (legacy_mappings or []):
        code = str(row.get("product_code", "")).strip()
        if not code:
            continue
        channel = str(row.get("channel", "")).strip().upper()
        bom = Bom(
            channel=channel,
            channel_name=str(row.get("channel_name", "")),
            option_name=str(row.get("option_name", "")),
            channel_product_id=str(row.get("channel_product_id", "")).strip(),
            channel_option_id=str(row.get("channel_option_id", "")).strip(),
            components=[Component(code, 1)],
            legacy=True,
        )
        if bom.channel_product_id:
            by_id.setdefault(_id_key(channel, bom.channel_product_id, bom.channel_option_id), bom)
            by_product.setdefault(_id_key(channel, bom.channel_product_id, ""), bom)
        name_key = _name_key(channel, bom.channel_name, bom.option_name)
        if name_key[1]:
            by_name.setdefault(name_key, bom)

    # 이름이 긴 것부터 맞춘다. '설탕 15kg 2개'가 '설탕 15kg'보다 먼저 걸려야 한다
    name_items = sorted(by_name.items(), key=lambda kv: -len(kv[0][1]))

    def resolve(channel: str, name: str = "", option: str = "",
                product_id: str = "", option_id: str = "") -> Bom | None:
        channel = str(channel or "").strip().upper()

        if product_id:
            hit = by_id.get(_id_key(channel, product_id, option_id))
            if hit:
                return _tag(hit, "상품번호+옵션ID" if option_id else "상품번호")
            if not option_id:
                hit = by_product.get(_id_key(channel, product_id, ""))
                if hit:
                    return _tag(hit, "상품번호")

        exact = by_name.get(_name_key(channel, name, option))
        if exact:
            return _tag(exact, "예전 연결" if exact.legacy else "상품명")

        hay = _norm(f"{name}{option}")
        if hay:
            for (ch, key), bom in name_items:
                if ch == channel and key and key in hay:
                    return _tag(bom, "예전 연결" if bom.legacy else "상품명")
        return None

    def _tag(bom: Bom, source: str) -> Bom:
        return Bom(
            channel=bom.channel, channel_name=bom.channel_name, option_name=bom.option_name,
            channel_product_id=bom.channel_product_id, channel_option_id=bom.channel_option_id,
            components=list(bom.components), source=source, legacy=bom.legacy,
        )

    return resolve


def to_rows(bom: Bom, created_by: str = "") -> list[dict]:
    """구성표를 시트에 넣을 줄로 바꾼다. 구성품 하나가 한 줄이다."""
    return [{
        "channel": bom.channel,
        "channel_product_id": bom.channel_product_id,
        "channel_option_id": bom.channel_option_id,
        "channel_name": bom.channel_name,
        "option_name": bom.option_name,
        "component_product_code": c.product_code,
        "component_qty": c.qty,
        "is_active": "TRUE",
        "created_by": created_by,
    } for c in bom.components]


def same_target(a: dict, bom: Bom) -> bool:
    """시트의 한 줄이 이 구성표와 같은 판매 SKU를 가리키는가."""
    if str(a.get("channel", "")).strip().upper() != bom.channel:
        return False
    if bom.channel_product_id:
        return (str(a.get("channel_product_id", "")).strip() == bom.channel_product_id
                and str(a.get("channel_option_id", "")).strip() == bom.channel_option_id)
    return (_norm(a.get("channel_name")) == _norm(bom.channel_name)
            and _norm(a.get("option_name")) == _norm(bom.option_name))


def describe(bom: Bom, names: dict[str, str]) -> str:
    """사람이 읽을 수 있게. names 는 상품코드 → 상품명."""
    return " + ".join(
        f"{names.get(c.product_code, c.product_code)} ×{c.qty}" for c in bom.components)
