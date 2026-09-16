"""
판매상품명을 보고 구성을 제안한다.

AI의 역할은 여기까지다. 제안한 구성이 곧바로 재고를 움직이지 않는다.
사람이 확인하고 저장한 뒤부터, 그 저장된 구성표로만 차감한다.

매번 추론하게 두면 같은 상품이 어제와 오늘 다르게 분해되어,
재고가 어디서부터 틀어졌는지 알 수 없게 된다.
"""
from __future__ import annotations

import json
import re

from .bom import Bom, Component

DEFAULT_MODEL = "claude-sonnet-5"
MAX_MASTER = 400   # 한 번에 보여줄 상품 수

PROMPT = """당신은 한국 온라인 쇼핑몰(쿠팡·네이버 스마트스토어)의 판매 상품명을 보고
그 상품이 창고의 어떤 실물 상품 몇 개로 이루어졌는지 알아내는 일을 합니다.

아래는 이 판매자가 창고에 두고 관리하는 실물 상품 목록입니다.

{master}

판매 상품명: {name}
옵션명: {option}

이 판매 상품이 팔렸을 때 창고에서 빠져나갈 실물 상품과 개수를 JSON으로만 답하세요.
설명이나 코드블록 표시 없이 JSON 객체 하나만 출력합니다.

{{
  "components": [
    {{"product_code": "위 목록의 상품코드", "qty": 판매 1개당 빠지는 개수, "why": "판단 근거 한 구절"}}
  ],
  "confidence": "high" 또는 "low",
  "note": "확신이 낮은 이유나 사람이 확인할 점. 없으면 \\"\\""
}}

지켜야 할 것:

- product_code 는 반드시 위 목록에 있는 것만 씁니다. 목록에 없는 상품을 지어내지 마세요.
- qty 는 **판매 1개당** 빠지는 개수입니다. 주문 수량은 곱하지 마세요.
  "A+B 2세트" 라면 A는 2, B는 2 입니다.
  "A 3개입" 이라면 A는 3 입니다.
- "2종세트", "3종세트" 는 들어가는 상품의 가짓수를 뜻합니다. 개수가 아닙니다.
  "A+B 2종세트" 는 A ×1, B ×1 입니다.
- 단품이면 구성품 한 개짜리로 답하세요. 그것도 정상입니다.
- 상품명만으로 어떤 상품이 들어가는지 알 수 없으면 confidence 를 "low" 로 두고,
  note 에 무엇을 확인해야 하는지 적으세요.
  "골라담기", "랜덤", "인기구성" 처럼 구성이 정해지지 않은 상품이 여기 해당합니다.
  이럴 때는 components 를 빈 배열로 두어도 됩니다. 억지로 채우지 마세요.
- 규격이 다르면 다른 상품입니다. 15kg와 3kg를 같은 것으로 보지 마세요."""


def master_lines(products: list[dict], limit: int = MAX_MASTER) -> str:
    """상품 마스터를 모델이 읽을 형태로. 활성 상품만 보낸다."""
    rows = []
    for p in products:
        if str(p.get("is_active", "TRUE")).upper() == "FALSE":
            continue
        parts = [str(p.get("product_code", "")), str(p.get("product_name", ""))]
        spec = str(p.get("spec", "") or "").strip()
        if spec:
            parts.append(spec)
        maker = str(p.get("maker", "") or "").strip()
        if maker:
            parts.append(maker)
        rows.append(" | ".join(parts))
        if len(rows) >= limit:
            break
    return "\n".join(rows)


def parse_response(text: str, known_codes: set[str]) -> tuple[Bom | None, str, str]:
    """
    모델 답을 구성표로 바꾼다.

    돌려주는 값: (구성표 또는 None, 확신도, 메모)

    목록에 없는 상품코드를 지어낸 경우 그 줄은 버린다. 없는 상품을 차감하면
    원장에 유령 상품이 생기고, 그때부터 재고 합계가 맞지 않는다.
    """
    body = str(text or "").strip()
    body = re.sub(r"^```(?:json)?\s*", "", body)
    body = re.sub(r"\s*```$", "", body)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("구성을 읽지 못했습니다. 직접 골라주세요.")

    try:
        data = json.loads(body[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("분석 결과를 읽지 못했습니다. 직접 골라주세요.") from exc

    components, dropped = [], []
    for raw in data.get("components") or []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("product_code", "")).strip()
        try:
            qty = int(float(raw.get("qty", 1)))
        except (ValueError, TypeError):
            qty = 1
        if qty <= 0:
            continue
        if code not in known_codes:
            dropped.append(code)
            continue
        components.append(Component(code, qty))

    confidence = "low" if str(data.get("confidence", "")).lower() == "low" else "high"
    note = str(data.get("note", "") or "").strip()
    if dropped:
        confidence = "low"
        note = (note + " " if note else "") + \
            f"목록에 없는 상품코드 {len(dropped)}건은 버렸습니다."

    if not components:
        return None, "low", note or "상품명만으로는 구성을 알 수 없습니다. 직접 골라주세요."

    return Bom(channel="", components=components), confidence, note


def propose(channel: str, name: str, option: str, products: list[dict],
            api_key: str, model: str = DEFAULT_MODEL) -> tuple[Bom | None, str, str]:
    """
    판매상품명 하나의 구성을 제안받는다.

    상품명만 보내지 않고 상품 마스터를 함께 보낸다. 그래야 모델이 창고에 없는
    상품을 지어내지 않고, 있는 것 중에서 고른다.
    """
    if not api_key:
        raise ValueError(
            "Claude API 키가 없습니다. Streamlit Secrets에 anthropic_api_key 를 넣거나, "
            "아래에서 구성품을 직접 고르세요."
        )
    try:
        import anthropic
    except ImportError as exc:
        raise ValueError("anthropic 라이브러리가 없습니다. requirements.txt 를 확인하세요.") from exc

    prompt = PROMPT.format(
        master=master_lines(products),
        name=name or "(없음)",
        option=option or "(없음)",
    )
    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=model, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise ValueError(f"구성 분석에 실패했습니다: {exc}") from exc

    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    known = {str(p.get("product_code", "")) for p in products}
    bom, confidence, note = parse_response(text, known)
    if bom is not None:
        bom.channel = str(channel or "").upper()
        bom.source = f"AI 제안 ({confidence})"
    return bom, confidence, note


def guess_offline(name: str, option: str, products: list[dict]) -> Bom | None:
    """
    API 키가 없을 때 쓰는 간단한 제안.

    판매상품명에 상품명이 통째로 들어 있는 경우만 잡는다. 수량이나 세트 구성은
    알아내지 못하므로 어디까지나 출발점이고, 사람이 고쳐야 한다.
    """
    hay = re.sub(r"[\s()\[\],./]", "", f"{name}{option}").lower()
    if not hay:
        return None

    found = []
    for p in products:
        if str(p.get("is_active", "TRUE")).upper() == "FALSE":
            continue
        label = re.sub(r"[\s()\[\],./]", "",
                       f"{p.get('product_name', '')}{p.get('spec', '')}").lower()
        if len(label) > 3 and label in hay:
            found.append(Component(str(p["product_code"]), 1))

    if not found:
        return None
    return Bom(channel="", components=found, source="이름 포함 (직접 확인 필요)")
