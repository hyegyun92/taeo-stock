"""
거래명세표 사진을 읽는다.

사진에서 읽은 내용은 그 자체로 재고를 움직이지 않는다. 사람이 화면에서 확인하고
입고 확정을 눌러야 원장에 들어간다. 특히 수기로 쓴 명세표는 잘못 읽히는 일이 잦다.

이 모듈은 읽기만 한다. 저장도, 매칭도 하지 않는다.
"""
from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field

from PIL import Image, ImageOps

# 폰으로 찍은 사진은 4MB를 넘기 일쑤다. 그대로 보내면 느리고 비싸다.
# 명세표는 글자를 읽어야 하므로 너무 줄이면 안 된다. 긴 변 1800px가 균형점이다.
MAX_EDGE = 1800
JPEG_QUALITY = 82
MAX_UPLOAD_BYTES = 12_000_000
MAX_PAGES = 5

DEFAULT_MODEL = "claude-sonnet-5"

PROMPT = """당신은 한국 식품·생활용품 도매 거래명세표를 읽는 일을 합니다.

이미지에서 아래 정보를 읽어 JSON으로만 답하세요. 설명이나 인사말, 코드블록 표시 없이
JSON 객체 하나만 출력합니다.

{
  "vendor_name": "공급자(파는 쪽) 상호. 태오상사는 받는 쪽이므로 절대 여기 적지 마세요",
  "invoice_date": "거래일자 YYYY-MM-DD. 못 읽으면 \\"\\"",
  "invoice_no": "거래명세표 번호. 없거나 못 읽으면 \\"\\"",
  "supply_amount": 공급가액 합계 숫자. 못 읽으면 null,
  "vat": 부가세 숫자. 못 읽으면 null,
  "total_amount": 총 금액 숫자. 못 읽으면 null,
  "is_return": 명세표 전체가 반품·상계 처리(수량이 음수)이면 true, 아니면 false,
  "items": [
    {
      "name": "상품명. 명세표에 적힌 그대로",
      "spec": "규격(15kg, 1.8L, 3kg/4 등). 상품명에 섞여 있으면 여기로 분리",
      "box_qty": 박스(BOX, 입수) 칸의 숫자. 그런 칸이 없으면 null,
      "qty": 총수량 칸의 숫자. 총수량 칸이 없으면 박스 수량을 그대로,
      "barcode": "제품코드나 바코드가 찍혀 있으면 숫자만. 없으면 \\"\\"",
      "unit_price": 단가 숫자 또는 null,
      "amount": 공급가액 숫자 또는 null,
      "confidence": "high" 또는 "low"
    }
  ],
  "note": "읽기 어려웠던 점이 있으면 한 문장. 없으면 \\"\\""
}

지켜야 할 것:

- 가장 중요한 것은 상품명, 규격, 수량입니다. 나머지는 못 읽으면 null로 두세요.
- **수량이 음수이면 음수 그대로 적으세요.** 반품이나 상계 처리 명세표에서는
  수량과 금액에 마이너스가 붙습니다. 부호를 떼거나 그 줄을 빼면 안 됩니다.
  수량 앞의 -, △, ▲ 는 모두 마이너스로 봅니다.
- 명세표 전체가 음수뿐이면 is_return 을 true 로 두세요.
- 박스 칸과 총수량 칸이 따로 있으면 둘 다 적으세요. 예를 들어 "3kg/4"가
  1박스면 총수량은 4입니다. 총수량 칸이 비어 있으면 qty에 박스 수량을 적고
  box_qty도 같은 값을 적으세요.
- 제품코드나 바코드가 줄마다 찍혀 있으면 반드시 적으세요. 상품을 알아보는
  가장 확실한 근거입니다.
- 같은 상품이 여러 줄에 나와도 합치지 마세요. 줄마다 따로 적습니다.
- 글자가 흐리거나 손글씨라 확신이 서지 않는 줄은 confidence를 "low"로 표시하세요.
  추측해서 채우지 말고, 읽은 대로 적고 low로 표시하는 편이 낫습니다.
- 합계, 소계, 부가세, 인수자, 공급가액 같은 줄은 상품이 아닙니다. items에 넣지 마세요.
- 수량이 비어 있거나 0인 줄은 items에서 빼세요.
- 숫자에서 쉼표를 없애고 숫자만 남기세요.
- 날짜가 2026.09.11 이나 26/09/11 처럼 적혀 있어도 YYYY-MM-DD로 바꿔 적으세요.
- 명세표가 아닌 사진이면 items를 빈 배열로 두고 note에 무엇이 찍혔는지 적으세요."""


@dataclass
class InvoiceItem:
    name: str
    spec: str = ""
    qty: int = 0              # 총수량. 반품이면 음수
    box_qty: int | None = None
    barcode: str = ""
    unit_price: float | None = None
    amount: float | None = None
    confidence: str = "high"

    @property
    def full_name(self) -> str:
        return f"{self.name} {self.spec}".strip()

    @property
    def is_return(self) -> bool:
        return self.qty < 0

    @property
    def per_box(self) -> int | None:
        """한 박스에 몇 개 들었는지. 박스 칸과 총수량 칸이 다 있을 때만 안다."""
        if not self.box_qty:
            return None
        if self.qty % self.box_qty != 0:
            return None
        return abs(self.qty // self.box_qty)


@dataclass
class InvoiceRead:
    vendor_name: str = ""
    invoice_date: str = ""
    invoice_no: str = ""
    supply_amount: float | None = None
    vat: float | None = None
    total_amount: float | None = None
    items: list[InvoiceItem] = field(default_factory=list)
    is_return: bool = False
    note: str = ""

    @property
    def low_confidence_count(self) -> int:
        return sum(1 for i in self.items if i.confidence == "low")

    @property
    def has_negative(self) -> bool:
        return any(i.qty < 0 for i in self.items)

    @property
    def mixed_signs(self) -> bool:
        """한 명세표에 입고와 반품이 섞여 있는가. 있으면 특히 조심해야 한다."""
        return any(i.qty > 0 for i in self.items) and any(i.qty < 0 for i in self.items)


# ---------------------------------------------------------------------
# 이미지 준비
# ---------------------------------------------------------------------
def prepare_image(raw: bytes) -> tuple[str, str]:
    """
    사진을 API에 보낼 수 있는 형태로 줄인다.

    폰 사진에는 회전 정보가 따로 들어 있어서, 그대로 보내면 옆으로 누운 채
    분석된다. ImageOps.exif_transpose 가 그것을 바로잡는다.

    돌려주는 값: (base64 문자열, media type)
    """
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("사진이 너무 큽니다. 12MB 이하로 찍어주세요.")

    try:
        image = Image.open(io.BytesIO(raw))
        image = ImageOps.exif_transpose(image)
    except Exception as exc:
        raise ValueError("사진을 읽지 못했습니다. JPG나 PNG로 다시 찍어주세요.") from exc

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    longest = max(image.size)
    if longest > MAX_EDGE:
        scale = MAX_EDGE / longest
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS,
        )

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii"), "image/jpeg"


# ---------------------------------------------------------------------
# 응답 읽기
# ---------------------------------------------------------------------
def _number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


NEGATIVE_MARKS = ("-", "\u2212", "\u25b3", "\u25b2", "(", "\u25bc")


def _int(value) -> int:
    """
    수량을 읽는다. 음수 부호를 살린다.

    반품·상계 명세표에서는 수량이 음수로 찍힌다. 부호를 떼면 돌려보낸 물건이
    들어온 것으로 기록되어, 실제와 두 배로 어긋난다.
    회계 서식에서 음수를 △나 괄호로 쓰기도 해서 함께 본다.
    """
    if value is None or value == "":
        return 0
    text = str(value).strip()
    negative = text.startswith(NEGATIVE_MARKS) or (text.startswith("(") and text.endswith(")"))
    number = _number(text)
    if number is None:
        return 0
    result = int(abs(number))
    return -result if (negative or number < 0) else result


DATE_RE = re.compile(r"(\d{4})\D(\d{1,2})\D(\d{1,2})")


def normalize_date(value: str) -> str:
    """2026.09.11 이나 26/9/11 같은 표기를 YYYY-MM-DD로."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = DATE_RE.search(text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    short = re.search(r"(\d{2})\D(\d{1,2})\D(\d{1,2})", text)
    if short:
        year, month, day = short.groups()
        return f"20{int(year):02d}-{int(month):02d}-{int(day):02d}"
    return ""


def parse_response(text: str) -> InvoiceRead:
    """
    모델이 돌려준 글에서 JSON을 꺼낸다.

    코드블록 표시를 붙이거나 앞뒤에 한 마디 덧붙이는 경우가 있어서,
    첫 중괄호부터 마지막 중괄호까지를 잘라 쓴다.
    """
    body = str(text or "").strip()
    body = re.sub(r"^```(?:json)?\s*", "", body)
    body = re.sub(r"\s*```$", "", body)

    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("명세표를 읽지 못했습니다. 다시 촬영해주세요.")

    try:
        data = json.loads(body[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("분석 결과를 읽지 못했습니다. 다시 시도해주세요.") from exc

    items = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        qty = _int(raw.get("qty"))
        name = str(raw.get("name", "")).strip()
        # 0만 버린다. 음수는 반품이므로 반드시 살린다.
        if qty == 0 or not name:
            continue
        box_qty = _int(raw.get("box_qty")) or None
        items.append(InvoiceItem(
            name=name,
            spec=str(raw.get("spec", "") or "").strip(),
            qty=qty,
            box_qty=box_qty,
            barcode=re.sub(r"\D", "", str(raw.get("barcode", "") or "")),
            unit_price=_number(raw.get("unit_price")),
            amount=_number(raw.get("amount")),
            confidence="low" if str(raw.get("confidence", "")).lower() == "low" else "high",
        ))

    return InvoiceRead(
        vendor_name=str(data.get("vendor_name", "") or "").strip(),
        invoice_date=normalize_date(data.get("invoice_date", "")),
        invoice_no=str(data.get("invoice_no", "") or "").strip(),
        supply_amount=_number(data.get("supply_amount")),
        vat=_number(data.get("vat")),
        total_amount=_number(data.get("total_amount")),
        items=items,
        is_return=bool(data.get("is_return")) or (
            bool(items) and all(i.qty < 0 for i in items)),
        note=str(data.get("note", "") or "").strip(),
    )


# ---------------------------------------------------------------------
# 호출
# ---------------------------------------------------------------------
def analyze(images: list[bytes], api_key: str, model: str = DEFAULT_MODEL) -> InvoiceRead:
    """
    사진 여러 장을 한 번에 보낸다.

    한 거래명세표가 두 장에 걸쳐 있는 경우가 흔해서, 장마다 따로 분석하면
    합계가 갈라진다. 함께 보내야 하나의 명세표로 읽는다.
    """
    if not images:
        raise ValueError("사진이 없습니다.")
    if not api_key:
        raise ValueError(
            "Claude API 키가 없습니다. Streamlit Secrets에 anthropic_api_key 를 넣어주세요."
        )
    if len(images) > MAX_PAGES:
        raise ValueError(f"사진은 한 번에 {MAX_PAGES}장까지 보낼 수 있습니다.")

    try:
        import anthropic
    except ImportError as exc:
        raise ValueError(
            "anthropic 라이브러리가 없습니다. requirements.txt 에 anthropic 을 넣고 다시 배포하세요."
        ) from exc

    content: list[dict] = []
    for raw in images:
        encoded, media_type = prepare_image(raw)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": encoded},
        })
    content.append({"type": "text", "text": PROMPT})

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=4000,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        raise ValueError(f"분석에 실패했습니다: {exc}") from exc

    text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    return parse_response(text)
