"""
시트 없이 돌릴 수 있는 검증.

    python tests/test_core.py

인터넷이나 구글 계정 없이 계산 로직과 파서를 전부 확인한다.
"""
from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from lib import parsers
from lib.inventory import Snapshot, fmt_left, fmt_rate, purchase_key, sale_key

TODAY = date(2026, 9, 10)
FAILS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  통과  {label}")
    else:
        print(f"  실패  {label}  {detail}")
        FAILS.append(label)


# ---------------------------------------------------------------------
def make_fixture():
    vendors = [
        {"vendor_code": "V01", "vendor_name": "대한식자재", "lead_days": 2, "cycle_days": 7, "min_amount": 300000},
        {"vendor_code": "V02", "vendor_name": "한성유통", "lead_days": 4, "cycle_days": 7, "min_amount": 500000},
    ]
    spec = [
        ("TAEO-00001", "백설 하얀설탕", "15kg", "V01", 23500, 6, 1, 2.0, 1.0),
        ("TAEO-00002", "해찬들 재래식된장", "14kg", "V01", 29800, 4, 1, 0.8, 0.3),
        ("TAEO-00003", "오뚜기 카레 순한맛", "1kg", "V02", 8900, 10, 5, 2.4, 0.15),
        ("TAEO-00004", "곰표 중력밀가루", "20kg", "V02", 21000, 5, 1, 1.1, 1.0),
        ("TAEO-00005", "다우니 실내건조", "8.5L", "V01", 29500, 4, 2, 0.0, 1.0),
    ]
    products = [{
        "product_code": c, "product_name": n, "spec": s, "maker": "테스트",
        "vendor_code": v, "purchase_price": price, "safety_stock": safety,
        "order_unit": unit, "unit_label": "개", "is_active": "TRUE",
    } for c, n, s, v, price, safety, unit, _, _ in spec]

    rng = random.Random(7)
    ledger, running = [], {}
    for c, _, _, _, _, safety, _, rate, keep in spec:
        opening = round(rate * 26 * keep + safety * 2) or 30
        running[c] = opening
        ledger.append({"txn_key": f"OPEN|{c}", "product_code": c, "qty_change": opening,
                       "reason": "OPENING", "occurred_on": (TODAY - timedelta(days=60)).isoformat(),
                       "party": "", "ref_no": "실사"})

    for ago in range(59, -1, -1):
        day = TODAY - timedelta(days=ago)
        for c, _, _, _, _, _, unit, rate, keep in spec:
            for channel, share in (("COUPANG", 0.65), ("NAVER", 0.35)):
                qty = min(int(rate * share + rng.random() * rate * share * 1.6), running[c])
                if qty <= 0:
                    continue
                running[c] -= qty
                order = f"{channel[0]}{day:%Y%m%d}{rng.randint(1000, 9999)}"
                ledger.append({"txn_key": sale_key(channel, order, c), "product_code": c,
                               "qty_change": -qty, "reason": channel, "party": channel,
                               "ref_no": order, "occurred_on": day.isoformat()})
            if day.weekday() in (1, 4) and rng.random() <= 0.42 * keep:
                qty = max(unit, round(rate * 9 * keep / unit) * unit)
                running[c] += qty
                invoice = f"S{day:%Y%m%d}"
                ledger.append({"txn_key": purchase_key("V01", invoice, 1, c), "product_code": c,
                               "qty_change": qty, "reason": "PURCHASE", "party": "대한식자재",
                               "ref_no": invoice, "occurred_on": day.isoformat()})
    return products, vendors, ledger


products, vendors, ledger = make_fixture()
snap = Snapshot(products, vendors, ledger, today=TODAY)

print("\n[1] 재고는 원장 합산으로만 나온다")
manual = {}
for row in ledger:
    manual[row["product_code"]] = manual.get(row["product_code"], 0) + row["qty_change"]
check("모든 상품이 손계산과 일치", all(snap.stock(c) == manual[c] for c in manual))
check("음수 재고 없음", all(snap.stock(c) >= 0 for c in snap.products),
      str({c: snap.stock(c) for c in snap.products if snap.stock(c) < 0}))

print("\n[2] 재고 현황")
for c in snap.products:
    print(f"     {snap.products[c]['product_name']:<20} 재고 {snap.stock(c):>4} "
          f"| 7일 {snap.sold7(c):>3} | 30일 {snap.sold30(c):>3} "
          f"| {fmt_rate(snap.daily_rate(c)):<12} | {fmt_left(snap.days_left(c)):<12} "
          f"| 발주 {snap.suggest_qty(c) if snap.needs_order(c) else '-'}")

print("\n[3] 발주 판단")
orders = snap.order_list()
check("발주 대상이 일부만", 0 < len(orders) < len(snap.products), f"{len(orders)}/{len(snap.products)}")
check("발주 대상 권장수량은 모두 양수", all(snap.suggest_qty(p["product_code"]) > 0 for p in orders))
check("발주 목록은 급한 순", [snap.days_left(p["product_code"]) for p in orders]
      == sorted(snap.days_left(p["product_code"]) for p in orders))
check("발주 배수 지킴", all(snap.suggest_qty(p["product_code"]) % int(p["order_unit"]) == 0 for p in orders))

print("\n[4] 안 팔리는 상품")
idle = "TAEO-00005"
check("판매 없으면 소진일 무한", snap.days_left(idle) == math.inf)
check("판매 없으면 문구가 '최근 판매 없음'", fmt_left(snap.days_left(idle)) == "최근 판매 없음")
check("판매 없어도 0으로 나눠 터지지 않음", isinstance(snap.suggest_qty(idle), int))

print("\n[5] 대량주문 한 건이 평균을 왜곡하지 않는다")
spike = [{"txn_key": "OPEN|X", "product_code": "X", "qty_change": 500, "reason": "OPENING",
          "occurred_on": (TODAY - timedelta(days=40)).isoformat()}]
for i in range(7):  # 최근 7일에만 몰아서 판매
    spike.append({"txn_key": f"S|X|{i}", "product_code": "X", "qty_change": -30,
                  "reason": "COUPANG", "occurred_on": (TODAY - timedelta(days=i)).isoformat()})
spike_products = [{"product_code": "X", "product_name": "명절상품", "spec": "", "vendor_code": "V01",
                   "purchase_price": 1000, "safety_stock": 0, "order_unit": 1, "is_active": "TRUE"}]
spike_snap = Snapshot(spike_products, vendors, spike, today=TODAY)
r7, r30 = spike_snap.sold7("X") / 7, spike_snap.sold30("X") / 30
check("고르게 오른 수요는 보정되지 않는다", spike_snap.spike_day("X") is None)
check("7일 가중이 반영된다", spike_snap.daily_rate("X") > r30,
      f"30일평균 {r30:.1f} → 적용 {spike_snap.daily_rate('X'):.1f}")

# 하루에만 대량주문이 몰린 경우
bulk = [{"txn_key": "OPEN|Z", "product_code": "Z", "qty_change": 500, "reason": "OPENING",
         "occurred_on": (TODAY - timedelta(days=40)).isoformat()}]
for i in range(7):
    qty = 200 if i == 3 else 2   # 나흘 전에 대량주문 한 건
    bulk.append({"txn_key": f"S|Z|{i}", "product_code": "Z", "qty_change": -qty,
                 "reason": "COUPANG", "occurred_on": (TODAY - timedelta(days=i)).isoformat()})
z_products = [{"product_code": "Z", "product_name": "대량주문상품", "spec": "", "vendor_code": "V01",
               "purchase_price": 1000, "safety_stock": 0, "order_unit": 1, "is_active": "TRUE"}]
bulk_snap = Snapshot(z_products, vendors, bulk, today=TODAY)
naive = bulk_snap.sold7("Z") / 7 * 0.6 + bulk_snap.sold30("Z") / 30 * 0.4
check("대량주문 날을 찾아낸다", bulk_snap.spike_day("Z") is not None,
      str(bulk_snap.spike_day("Z")))
check("보정 없이 계산한 값보다 훨씬 낮다", bulk_snap.daily_rate("Z") < naive / 3,
      f"보정없음 {naive:.1f} → 적용 {bulk_snap.daily_rate('Z'):.1f}")
check("평소 판매량 수준으로 수렴", bulk_snap.daily_rate("Z") < 5,
      f"{bulk_snap.daily_rate('Z'):.2f}")

print("\n[6] 취소·반품은 판매량에서 뺀다")
base = [{"txn_key": "OPEN|Y", "product_code": "Y", "qty_change": 100, "reason": "OPENING",
         "occurred_on": (TODAY - timedelta(days=30)).isoformat()},
        {"txn_key": "S|Y|1", "product_code": "Y", "qty_change": -10, "reason": "COUPANG",
         "occurred_on": (TODAY - timedelta(days=1)).isoformat()}]
y_products = [{"product_code": "Y", "product_name": "테스트", "spec": "", "vendor_code": "V01",
               "purchase_price": 100, "safety_stock": 0, "order_unit": 1, "is_active": "TRUE"}]
before = Snapshot(y_products, vendors, base, today=TODAY)
after = Snapshot(y_products, vendors, base + [{"txn_key": "R|Y|1", "product_code": "Y",
                 "qty_change": 4, "reason": "RETURN",
                 "occurred_on": (TODAY - timedelta(days=1)).isoformat()}], today=TODAY)
check("반품하면 재고가 늘어난다", after.stock("Y") == before.stock("Y") + 4)
check("반품하면 판매량은 줄어든다", after.sold7("Y") == before.sold7("Y") - 4,
      f"{before.sold7('Y')} → {after.sold7('Y')}")

print("\n[7] 비활성 상품은 집계에서 빠진다")
hidden = products + [{"product_code": "TAEO-09999", "product_name": "단종품", "spec": "",
                      "vendor_code": "V01", "purchase_price": 1000, "safety_stock": 99,
                      "order_unit": 1, "is_active": "FALSE"}]
hidden_snap = Snapshot(hidden, vendors, ledger, today=TODAY)
check("발주 목록에 안 나옴", all(p["product_code"] != "TAEO-09999" for p in hidden_snap.order_list()))
check("이력 조회는 여전히 가능", "TAEO-09999" in hidden_snap.all_products)

# ---------------------------------------------------------------------
print("\n[8] 쿠팡 파일 파싱")
coupang = pd.DataFrame({
    "주문번호": ["3000012345", "3000012346", "3000012347", "3000012348"],
    "노출상품명": ["테스트 백설 하얀설탕 15kg", "테스트 오뚜기 카레 순한맛 1kg",
                "듣도보도못한상품 999호", "테스트 곰표 중력밀가루 20kg"],
    "옵션명": ["15kg", "1kg", "999", "20kg"],
    "구매수량": ["2", "1", "3", "1"],
    "배송상태": ["배송완료", "배송완료", "배송완료", "결제취소"],
})
matcher = parsers.build_matcher(products, [])
columns = {k: parsers.guess_column(list(coupang.columns), k) for k in
           ("order", "name", "option", "qty", "status", "date")}
check("상품명 열 자동 인식", columns["name"] == "노출상품명", str(columns["name"]))
check("수량 열 자동 인식", columns["qty"] == "구매수량", str(columns["qty"]))

parsed = parsers.parse_sales(coupang, "COUPANG", columns, matcher, set(), TODAY)
for _, r in parsed.iterrows():
    print(f"     {r['qty']} {r['raw_name'][:30]:<32} → {r['status']:<8} {r['why']}")
check("설탕 매칭", parsed.iloc[0]["product_code"] == "TAEO-00001")
check("카레 매칭", parsed.iloc[1]["product_code"] == "TAEO-00003")
check("모르는 상품은 매칭실패", parsed.iloc[2]["status"] == "매칭실패")
check("취소건은 따로 표시", parsed.iloc[3]["status"] == "취소건")

print("\n[9] 같은 파일을 두 번 올려도 중복되지 않는다")
applied = {r["txn_key"] for _, r in parsed.iterrows() if r["status"] == "반영"}
again = parsers.parse_sales(coupang, "COUPANG", columns, matcher, applied, TODAY)
check("두 번째는 전부 중복 처리", all(r["status"] != "반영" for _, r in again.iterrows()),
      str(again["status"].tolist()))

print("\n[10] 등록된 판매처 이름이 유사도보다 우선한다")
mappings = [{"product_code": "TAEO-00002", "channel": "COUPANG",
             "channel_name": "듣도보도못한상품 999호", "option_name": "999",
             "channel_product_id": "", "channel_option_id": ""}]
mapped = parsers.build_matcher(products, mappings)
code, why = mapped("COUPANG", "듣도보도못한상품 999호", "999")
check("등록한 이름으로 잡힘", code == "TAEO-00002", f"{code} / {why}")
check("근거가 표시됨", why == "등록된 이름", why)

print("\n[11] 상품번호가 있으면 그것으로 먼저 맞춘다")
id_mappings = [{"product_code": "TAEO-00004", "channel": "NAVER", "channel_name": "아무 이름",
                "option_name": "", "channel_product_id": "77889900", "channel_option_id": ""}]
id_matcher = parsers.build_matcher(products, id_mappings)
code, why = id_matcher("NAVER", "전혀 다른 이름", "", channel_id="77889900")
check("상품번호로 매칭", code == "TAEO-00004" and why == "상품번호", f"{code} / {why}")

print("\n[12] 네이버는 상품주문번호를 주문번호보다 먼저 쓴다")
naver = pd.DataFrame({
    "주문번호": ["2026091012345", "2026091012345"],
    "상품주문번호": ["2026091012345001", "2026091012345002"],
    "상품명": ["테스트 백설 하얀설탕 15kg", "테스트 곰표 중력밀가루 20kg"],
    "수량": ["1", "2"],
    "주문상태": ["발송완료", "발송완료"],
})
n_columns = {k: parsers.guess_column(list(naver.columns), k) for k in
             ("order", "name", "option", "qty", "status", "date")}
check("상품주문번호를 고름", n_columns["order"] == "상품주문번호", str(n_columns["order"]))
n_parsed = parsers.parse_sales(naver, "NAVER", n_columns, matcher, set(), TODAY)
keys = n_parsed["txn_key"].tolist()
check("한 주문의 두 상품이 서로 다른 키", len(set(keys)) == 2, str(keys))

print("\n[13] 머리글 위에 안내문이 붙어도 찾아낸다")
messy = "쿠팡 판매내역 다운로드\n조회기간 2026-09-01 ~ 2026-09-10\n\n주문번호,상품명,수량,배송상태\n1,테스트 백설 하얀설탕 15kg,3,배송완료\n"
frame = parsers.read_table(type("F", (), {"name": "x.csv", "read": lambda self: messy.encode()})())
check("머리글 줄을 찾음", "주문번호" in frame.columns, str(list(frame.columns)))
check("데이터 1줄", len(frame) == 1, str(len(frame)))

print("\n[14] 거래명세표 표 붙여넣기")
invoice = "상품명\t수량\n테스트 백설 하얀설탕 15kg\t10\n테스트 다우니 실내건조 8.5L\t4\n합계\t\n"
rows = parsers.parse_invoice_text(invoice)
check("수량 없는 합계 줄은 버림", len(rows) == 2, str(len(rows)))
check("수량을 숫자로 읽음", rows.iloc[0]["qty"] == 10)

print("\n[15] 고유키 형식")
check("입고 키", purchase_key("V01", "S20260910", 1, "TAEO-00001") == "IN|V01|S20260910|1|TAEO-00001")
check("판매 키에 이벤트 종류 포함", sale_key("NAVER", "123", "TAEO-1", "RETURN").endswith("|RETURN"))
check("같은 주문의 판매와 반품은 다른 키",
      sale_key("NAVER", "123", "TAEO-1") != sale_key("NAVER", "123", "TAEO-1", "RETURN"))

print("\n" + ("실패 " + str(len(FAILS)) + "건: " + ", ".join(FAILS) if FAILS else "전부 통과"))
sys.exit(1 if FAILS else 0)
