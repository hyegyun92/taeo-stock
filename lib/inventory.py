"""
재고 계산.

이 모듈에는 시트나 화면에 대한 의존이 없다. 딕셔너리 리스트를 받아
숫자를 돌려줄 뿐이다. 그래서 인터넷 없이도 테스트할 수 있다.

가장 중요한 규칙: 현재재고를 저장하지 않는다. 원장을 더해서 만든다.
이 파일 어디에도 재고에 값을 대입하는 코드는 없다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

# 재고변동 사유. 시트의 stock_ledger.reason 값과 1:1로 맞춘다.
REASONS: dict[str, str] = {
    "OPENING": "초기재고",
    "PURCHASE": "매입입고",
    "COUPANG": "쿠팡판매",
    "NAVER": "네이버판매",
    "CANCEL": "판매취소",
    "RETURN": "반품입고",
    "DISPOSE": "폐기",
    "DAMAGE": "파손",
    "ADJUST": "재고조정",
}
SALE_REASONS = {"COUPANG", "NAVER"}
RESTORE_REASONS = {"CANCEL", "RETURN"}

# 발주 판단 기준: 남은 일수가 리드타임 + 이 값보다 짧으면 발주 대상
ORDER_BUFFER_DAYS = 3
# 하루 판매량이 이보다 작으면 "거의 안 나가는 상품"으로 본다
IDLE_RATE = 0.02


def _d(value) -> date | None:
    """시트에서 온 날짜 문자열을 date로. 못 읽으면 None."""
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        y, m, dd = text.split("-")
        return date(int(y), int(m), int(dd))
    except (ValueError, TypeError):
        return None


def _i(value, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError, AttributeError):
        return default


def _f(value, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError, AttributeError):
        return default


class Snapshot:
    """
    상품 · 매입처 · 원장을 한 번 훑어 필요한 수치를 미리 계산해 둔다.

    화면을 그릴 때마다 원장을 반복해서 도는 대신, 여기서 한 번만 돈다.
    원장이 수만 줄이 되어도 화면이 느려지지 않는다.
    """

    def __init__(self, products, vendors, ledger, today: date | None = None):
        self.today = today or date.today()
        self.products = {p["product_code"]: p for p in products if str(p.get("is_active", "TRUE")).upper() != "FALSE"}
        self.all_products = {p["product_code"]: p for p in products}
        self.vendors = {v["vendor_code"]: v for v in vendors}
        self.ledger = ledger

        self.on_hand: dict[str, int] = defaultdict(int)
        self._sold7: dict[str, int] = defaultdict(int)
        self._sold30: dict[str, int] = defaultdict(int)
        # 최근 7일을 날짜별로 따로 센다. 대량주문 한 건이 평균을 밀어올리는지 보려면
        # 합계만으로는 알 수 없고 하루하루가 필요하다.
        self._by_day: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))
        self.last_purchase: dict[str, date] = {}
        self.last_sale: dict[str, date] = {}
        self.today_sold = 0

        d7 = self.today - timedelta(days=6)
        d30 = self.today - timedelta(days=29)

        for row in ledger:
            code = row.get("product_code")
            if not code:
                continue
            qty = _i(row.get("qty_change"))
            self.on_hand[code] += qty

            reason = row.get("reason", "")
            when = _d(row.get("occurred_on"))
            if when is None:
                continue

            if reason in SALE_REASONS:
                sold = -qty  # 판매는 음수로 기록되어 있다
                if when >= d7:
                    self._sold7[code] += sold
                    self._by_day[code][when] += sold
                if when >= d30:
                    self._sold30[code] += sold
                if when == self.today:
                    self.today_sold += sold
                if code not in self.last_sale or when > self.last_sale[code]:
                    self.last_sale[code] = when
            elif reason in RESTORE_REASONS:
                # 취소·반품은 그만큼 팔리지 않은 것으로 본다
                if when >= d7:
                    self._sold7[code] -= qty
                    self._by_day[code][when] -= qty
                if when >= d30:
                    self._sold30[code] -= qty
                if when == self.today:
                    self.today_sold -= qty
            elif reason == "PURCHASE":
                if code not in self.last_purchase or when > self.last_purchase[code]:
                    self.last_purchase[code] = when

    # ---------- 조회 ----------
    def stock(self, code: str) -> int:
        return self.on_hand.get(code, 0)

    def sold7(self, code: str) -> int:
        return max(0, self._sold7.get(code, 0))

    def sold30(self, code: str) -> int:
        return max(0, self._sold30.get(code, 0))

    def vendor_of(self, code: str) -> dict:
        product = self.all_products.get(code, {})
        return self.vendors.get(product.get("vendor_code"), {
            "vendor_name": product.get("vendor_code", "미지정"),
            "lead_days": 3, "cycle_days": 7, "min_amount": 0,
        })

    def daily_rate(self, code: str) -> float:
        """
        최근 7일에 60%, 30일에 40% 가중.

        식품은 명절과 계절을 타기 때문에 단순평균보다 최근에 무게를 둔다.

        한 가지 보정이 있다. 어느 하루에 대량주문이 한 건 들어오면 그 하루가
        7일 평균을 통째로 밀어올려, 평소 하루 2개 나가는 상품을 20개씩 발주하게 된다.
        그래서 하루치가 7일 합계의 절반을 넘으면 그날을 빼고 나머지 6일로 계산한다.
        꾸준히 늘어나는 수요는 여러 날에 걸쳐 오르므로 이 보정에 걸리지 않는다.
        """
        total7 = self.sold7(code)
        days = self._by_day.get(code) or {}
        peak = max(days.values(), default=0)

        if peak > 0 and total7 > 0 and peak > total7 * 0.5 and len(days) >= 2:
            r7 = max(0, total7 - peak) / 6.0
        else:
            r7 = total7 / 7.0

        r30 = self.sold30(code) / 30.0
        return r7 * 0.6 + r30 * 0.4

    def spike_day(self, code: str) -> tuple[date, int] | None:
        """평균 계산에서 제외된 대량주문 날. 화면에 알려주기 위한 것."""
        days = self._by_day.get(code) or {}
        if not days:
            return None
        total = self.sold7(code)
        when = max(days, key=lambda d: days[d])
        if total > 0 and days[when] > total * 0.5 and len(days) >= 2:
            return when, days[when]
        return None

    def days_left(self, code: str) -> float:
        rate = self.daily_rate(code)
        if rate <= IDLE_RATE:
            return math.inf
        return self.stock(code) / rate

    def suggest_qty(self, code: str) -> int:
        """리드타임 + 발주주기 동안 팔 양 + 안전재고 - 현재재고. 발주 배수로 올림."""
        product = self.all_products.get(code)
        if not product:
            return 0
        vendor = self.vendor_of(code)
        horizon = _i(vendor.get("lead_days"), 3) + _i(vendor.get("cycle_days"), 7)
        need = self.daily_rate(code) * horizon + _i(product.get("safety_stock")) - self.stock(code)
        if need <= 0:
            return 0
        unit = max(1, _i(product.get("order_unit"), 1))
        return math.ceil(need / unit) * unit

    def needs_order(self, code: str) -> bool:
        product = self.all_products.get(code)
        if not product:
            return False
        stock = self.stock(code)
        if stock <= 0:
            return True
        if stock <= _i(product.get("safety_stock")):
            return True
        lead = _i(self.vendor_of(code).get("lead_days"), 3)
        return self.days_left(code) < lead + ORDER_BUFFER_DAYS

    def health(self, code: str) -> str:
        """ok / low / out"""
        if self.stock(code) <= 0:
            return "out"
        if self.needs_order(code):
            return "low"
        return "ok"

    def stock_value(self, code: str) -> float:
        product = self.all_products.get(code, {})
        return max(0, self.stock(code)) * _f(product.get("purchase_price"))

    # ---------- 요약 ----------
    def order_list(self) -> list[dict]:
        """발주가 급한 순서대로."""
        rows = [p for c, p in self.products.items() if self.needs_order(c)]
        return sorted(rows, key=lambda p: self.days_left(p["product_code"]))

    def totals(self) -> dict:
        codes = list(self.products)
        return {
            "products": len(codes),
            "units": sum(max(0, self.stock(c)) for c in codes),
            "value": sum(self.stock_value(c) for c in codes),
            "out": sum(1 for c in codes if self.stock(c) <= 0),
            "low": sum(1 for c in codes if self.health(c) == "low"),
            "order": sum(1 for c in codes if self.needs_order(c)),
            "today_sold": self.today_sold,
        }

    def history(self, code: str, limit: int = 30) -> list[dict]:
        rows = [r for r in self.ledger if r.get("product_code") == code]
        rows.sort(key=lambda r: (str(r.get("occurred_on", "")), str(r.get("created_at", ""))), reverse=True)
        return rows[:limit]

    def issues(self) -> list[tuple[str, str]]:
        """장부가 어긋난 신호. 매일 한 번 보면 사고를 일찍 잡는다."""
        found = []
        for code, product in self.products.items():
            stock = self.stock(code)
            name = product.get("product_name", code)
            if stock < 0:
                found.append((name, "재고가 음수입니다. 판매가 입고보다 먼저 들어왔는지 확인하세요."))
            elif stock == 0 and self.sold7(code) > 0:
                found.append((name, "품절인데 최근 판매가 있습니다. 판매처 재고를 내려야 합니다."))
            elif self.sold30(code) == 0 and stock > 0 and self.last_sale.get(code) is None:
                found.append((name, "판매 기록이 한 번도 없습니다. 판매처 상품명이 연결됐는지 확인하세요."))
        return found


# ---------- 표시용 ----------
def fmt_days(value: float) -> str:
    if value == math.inf:
        return "판매 없음"
    if value < 1:
        return "오늘 소진"
    return f"{int(value)}일"


def fmt_left(value: float) -> str:
    """문장 안에 넣을 때. '오늘 소진 남음' 같은 말이 나오지 않게 한다."""
    if value == math.inf:
        return "최근 판매 없음"
    if value < 1:
        return "오늘 안에 소진"
    return f"{int(value)}일 남음"


def fmt_rate(rate: float) -> str:
    if rate <= IDLE_RATE:
        return "거의 안 나감"
    if rate < 1:
        return f"하루 {rate:.2f}개"
    return f"하루 {rate:.1f}개"


def won(value: float) -> str:
    return f"{round(value):,}원"


# ---------- 거래 고유키 ----------
def purchase_key(vendor_code: str, invoice_no: str, line_no: int, product_code: str) -> str:
    """같은 명세표를 다시 올려도 재고가 부풀지 않게 하는 열쇠."""
    return f"IN|{vendor_code}|{invoice_no}|{line_no}|{product_code}"


def sale_key(channel: str, order_no: str, item_id: str, event: str = "SALE") -> str:
    """
    네이버는 주문번호가 아니라 상품주문번호를 써야 한다.
    주문번호는 여러 상품을 묶는 상위 개념이라 한 주문에 여러 줄이 생긴다.

    event를 키에 넣는 이유는 부분취소·부분반품 때문이다.
    같은 주문의 판매 / 취소 / 반품이 각각 별도 줄로 남는다.
    """
    return f"{channel}|{order_no}|{item_id}|{event}"


def manual_key(reason: str, product_code: str, stamp: str) -> str:
    return f"MAN|{reason}|{product_code}|{stamp}"
