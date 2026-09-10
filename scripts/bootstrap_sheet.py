"""
스프레드시트에 탭과 머리글을 만든다. 처음 한 번만 실행한다.

    python scripts/bootstrap_sheet.py --key <스프레드시트ID> --creds service_account.json
    python scripts/bootstrap_sheet.py --key <ID> --creds service_account.json --demo

--demo 를 붙이면 예시 상품과 60일치 판매 기록이 들어간다. 화면이 어떻게
돌아가는지 먼저 보고 싶을 때 쓰고, 실제로 장사에 쓸 때는 붙이지 않는다.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gspread
from google.oauth2.service_account import Credentials

from lib.sheets import SCHEMA, SCOPES


def ensure_tabs(book: gspread.Spreadsheet) -> None:
    existing = {ws.title for ws in book.worksheets()}
    for name, header in SCHEMA.items():
        if name in existing:
            worksheet = book.worksheet(name)
            current = worksheet.row_values(1)
            if current != header:
                worksheet.update([header], "A1")
                print(f"  {name}: 머리글 맞춤")
            else:
                print(f"  {name}: 그대로 둠")
            continue
        worksheet = book.add_worksheet(title=name, rows=2000, cols=max(12, len(header)))
        worksheet.update([header], "A1")
        worksheet.freeze(rows=1)
        worksheet.format("A1:Z1", {"textFormat": {"bold": True},
                                   "backgroundColor": {"red": .93, "green": .95, "blue": .94}})
        print(f"  {name}: 만듦")

    # gspread가 처음 만드는 기본 시트는 지운다
    if "시트1" in existing or "Sheet1" in existing:
        for title in ("시트1", "Sheet1"):
            try:
                book.del_worksheet(book.worksheet(title))
                print(f"  {title}: 비어 있어 지움")
            except gspread.WorksheetNotFound:
                pass


DEMO_VENDORS = [
    ("V01", "대한식자재", 2, 7, 300000),
    ("V02", "한성유통", 4, 7, 500000),
    ("V03", "성일생활용품", 3, 14, 200000),
]

# 상품명, 규격, 제조사, 매입처, 단가, 안전재고, 발주배수, 단위, 하루판매, 보충강도
DEMO_PRODUCTS = [
    ("백설 하얀설탕", "15kg", "CJ제일제당", "V01", 23500, 6, 1, "박스", 1.9, 1.05),
    ("해찬들 재래식된장", "14kg", "CJ제일제당", "V01", 29800, 4, 1, "통", 0.8, 0.34),
    ("순창 태양초고추장", "14kg", "대상", "V01", 41000, 4, 1, "통", 0.7, 0.95),
    ("백설 콩기름", "18L", "CJ제일제당", "V01", 39500, 5, 1, "말", 1.4, 0.62),
    ("곰표 중력밀가루", "20kg", "대한제분", "V02", 21000, 5, 1, "포", 1.1, 1.00),
    ("샘표 진간장 501", "15L", "샘표", "V02", 28900, 4, 1, "말", 0.6, 0.90),
    ("오뚜기 카레 순한맛", "1kg", "오뚜기", "V02", 8900, 10, 5, "개", 2.4, 0.18),
    ("청정원 맛술", "1.8L", "대상", "V02", 7200, 10, 5, "개", 1.2, 1.10),
    ("종가집 포기김치", "10kg", "대상", "V01", 38000, 3, 1, "박스", 0.9, 0.48),
    ("CJ 다시다 쇠고기", "1kg", "CJ제일제당", "V01", 13800, 8, 3, "개", 1.6, 0.97),
    ("유한락스 레귤러", "4L", "유한양행", "V03", 6800, 8, 4, "개", 1.0, 1.00),
    ("크리넥스 데코앤소프트", "30롤", "유한킴벌리", "V03", 24900, 6, 2, "팩", 1.3, 0.55),
    ("다우니 실내건조", "8.5L", "P&G", "V03", 29500, 4, 2, "통", 0.5, 0.88),
    ("페브리즈 섬유탈취제", "800ml", "P&G", "V03", 7900, 10, 5, "개", 1.7, 0.30),
    ("코멧 위생장갑", "200매", "쿠팡", "V03", 3900, 15, 10, "박스", 2.1, 1.15),
]


def build_demo(today: date) -> dict[str, list[list]]:
    rng = random.Random(20260910)
    stamp = datetime.now().isoformat(timespec="seconds")

    vendors = [[c, n, lead, cycle, minimum, "", ""] for c, n, lead, cycle, minimum in DEMO_VENDORS]

    products, mappings, ledger = [], [], []
    running: dict[str, int] = {}

    for i, (name, spec, maker, vendor, price, safety, unit_qty, unit, rate, keep) in enumerate(DEMO_PRODUCTS, 1):
        code = f"TAEO-{i:05d}"
        products.append([code, name, spec, maker, vendor, f"880{1000000 + i * 7919:07d}"[:13],
                         unit, unit_qty, price, safety, "TRUE", stamp])
        mappings.append([code, "COUPANG", "", "", f"{maker} {name} {spec}", spec, stamp])
        mappings.append([code, "NAVER", "", "", f"{name} {spec} 대용량", spec, stamp])

        opening = round(rate * 26 * keep + safety * 2)
        running[code] = opening
        ledger.append([f"OPEN|{code}", code, opening, "OPENING", "",
                       "시스템 시작 실사", (today - timedelta(days=60)).isoformat(), "사장님", stamp])

    for days_ago in range(59, -1, -1):
        day = today - timedelta(days=days_ago)
        weekend = 0.55 if day.weekday() >= 5 else 1.0

        for i, (name, spec, maker, vendor, price, safety, unit_qty, unit, rate, keep) in enumerate(DEMO_PRODUCTS, 1):
            code = f"TAEO-{i:05d}"
            for channel, share in (("COUPANG", 0.65), ("NAVER", 0.35)):
                lam = rate * share * weekend
                qty = int(lam + rng.random() * lam * 1.6)
                if rng.random() < 0.12:
                    qty += round(lam)
                qty = min(qty, running[code])  # 품절이면 팔리지 않는다
                if qty <= 0:
                    continue
                running[code] -= qty
                order_no = f"{'C' if channel == 'COUPANG' else 'N'}{day:%Y%m%d}{rng.randint(1000, 9999)}"
                ledger.append([f"{channel}|{order_no}|{code}|SALE", code, -qty, channel,
                               "쿠팡" if channel == "COUPANG" else "네이버",
                               order_no, day.isoformat(), "자동", stamp])

            if day.weekday() in (1, 4) and rng.random() <= 0.42 * keep:
                qty = max(unit_qty, round(rate * 9 * keep / unit_qty) * unit_qty)
                running[code] += qty
                invoice = f"S{day:%Y%m%d}{vendor[1:]}"
                vendor_name = dict((v[0], v[1]) for v in DEMO_VENDORS)[vendor]
                ledger.append([f"IN|{vendor}|{invoice}|1|{code}", code, qty, "PURCHASE",
                               vendor_name, invoice, day.isoformat(), "사장님", stamp])

    return {"vendor": vendors, "product": products,
            "channel_mapping": mappings, "stock_ledger": ledger}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True, help="스프레드시트 ID (주소의 /d/ 와 /edit 사이)")
    parser.add_argument("--creds", required=True, help="서비스 계정 JSON 파일 경로")
    parser.add_argument("--demo", action="store_true", help="예시 데이터를 함께 넣는다")
    args = parser.parse_args()

    info = json.loads(Path(args.creds).read_text(encoding="utf-8"))
    client = gspread.authorize(Credentials.from_service_account_info(info, scopes=SCOPES))
    book = client.open_by_key(args.key)

    print(f"스프레드시트: {book.title}")
    print("탭 확인")
    ensure_tabs(book)

    if args.demo:
        ledger_rows = len(book.worksheet("stock_ledger").col_values(1))
        if ledger_rows > 1:
            print("\n원장에 이미 기록이 있어 예시 데이터를 넣지 않았습니다.")
            print("정말 넣으려면 stock_ledger 탭을 비운 뒤 다시 실행하세요.")
        else:
            print("\n예시 데이터 넣는 중")
            demo = build_demo(date.today())
            for name in ("vendor", "product", "channel_mapping", "stock_ledger"):
                book.worksheet(name).append_rows(demo[name], value_input_option="USER_ENTERED")
                print(f"  {name}: {len(demo[name]):,}줄")

    print(f"\n완료. 이 ID를 Streamlit Secrets의 sheet_key 에 넣으세요:\n  {args.key}")
    print(f"서비스 계정 이메일에 편집 권한을 줬는지 확인하세요:\n  {info.get('client_email')}")


if __name__ == "__main__":
    main()
