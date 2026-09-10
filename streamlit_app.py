"""
태오상사 재고 장부 — Streamlit

원칙은 하나다. 현재재고를 어디에도 저장하지 않는다.
원장(stock_ledger)에 줄을 더해서 만든다. 이 파일에 재고를 대입하는 코드는 없다.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import parsers
from lib.inventory import (
    REASONS, Snapshot, fmt_days, fmt_left, fmt_rate, won,
    manual_key, purchase_key,
)
from lib.sheets import Store, today_kst, now_kst

st.set_page_config(
    page_title="태오상사 재고",
    page_icon="📦",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------
# 모바일 손질
#   Streamlit 기본값은 데스크톱 기준이라 폰에서 여백이 크고 글자가 작다.
# ---------------------------------------------------------------------
st.markdown("""
<style>
  #MainMenu, footer, header [data-testid="stToolbar"] {visibility:hidden;}
  .block-container {padding:0.8rem 0.9rem 4rem; max-width:640px;}
  h1 {font-size:1.35rem !important; margin-bottom:0.2rem;}
  h2 {font-size:1.1rem !important; margin:1.1rem 0 0.4rem;}
  h3 {font-size:0.98rem !important;}
  /* 탭을 가로 스크롤로. 폰에서 5개가 줄바꿈되지 않게 */
  .stTabs [data-baseweb="tab-list"] {gap:2px; overflow-x:auto; flex-wrap:nowrap;}
  .stTabs [data-baseweb="tab"] {padding:8px 12px; font-size:0.86rem; white-space:nowrap;}
  /* 손가락으로 누를 수 있는 크기 */
  .stButton button, .stDownloadButton button {width:100%; min-height:44px; font-weight:600;}
  .stTextInput input, .stNumberInput input, .stDateInput input {min-height:42px; font-size:16px;}
  /* 16px 미만이면 iOS가 입력할 때 화면을 확대해버린다 */
  [data-testid="stMetricValue"] {font-size:1.5rem;}
  [data-testid="stMetricLabel"] {font-size:0.78rem;}
  .stDataFrame {font-size:0.8rem;}
  /* 재고 상태 띠 */
  .item {display:flex; align-items:center; gap:10px; padding:9px 0;
         border-bottom:1px solid rgba(128,128,128,.22);}
  .item .bar {width:4px; align-self:stretch; min-height:34px; border-radius:2px;}
  .item .bar.ok{background:#0F5C4A;} .item .bar.low{background:#B8860B;} .item .bar.out{background:#A8331A;}
  .item .txt {flex:1; min-width:0;}
  .item .nm {font-weight:600; font-size:0.92rem; white-space:nowrap;
             overflow:hidden; text-overflow:ellipsis;}
  .item .mt {font-size:0.76rem; opacity:.62;}
  .item .qt {text-align:right; font-weight:700; font-size:1.05rem;
             font-variant-numeric:tabular-nums; white-space:nowrap;}
  .item .qt.low{color:#B8860B;} .item .qt.out{color:#A8331A;}
  .item .qt small {display:block; font-size:0.7rem; font-weight:400; opacity:.62;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------
# 연결
# ---------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_store() -> Store:
    if "gcp_service_account" not in st.secrets or "sheet_key" not in st.secrets:
        st.error(
            "설정이 없습니다. Streamlit Cloud의 Settings → Secrets 에 "
            "`sheet_key` 와 `[gcp_service_account]` 를 넣어주세요. "
            "자세한 순서는 README에 있습니다."
        )
        st.stop()
    return Store(dict(st.secrets["gcp_service_account"]), st.secrets["sheet_key"])


@st.cache_data(ttl=180, show_spinner="장부를 읽는 중")
def load_data(_version: int) -> dict:
    return get_store().read_all()


def refresh() -> None:
    """장부를 고쳤을 때 캐시를 버린다."""
    st.session_state.data_version = st.session_state.get("data_version", 0) + 1


def get_snapshot() -> tuple[Snapshot, dict]:
    data = load_data(st.session_state.get("data_version", 0))
    snap = Snapshot(data["product"], data["vendor"], data["stock_ledger"], today=today_kst())
    return snap, data


def item_row(snap: Snapshot, code: str, right: str, sub: str | None = None) -> None:
    p = snap.all_products.get(code, {})
    h = snap.health(code)
    meta = sub if sub is not None else f"{p.get('spec', '')} · {p.get('maker', '')}"
    st.markdown(
        f"<div class='item'><div class='bar {h}'></div>"
        f"<div class='txt'><div class='nm'>{p.get('product_name', code)}</div>"
        f"<div class='mt'>{meta}</div></div>"
        f"<div class='qt {h}'>{snap.stock(code):,}<small>{right}</small></div></div>",
        unsafe_allow_html=True,
    )


def product_options(snap: Snapshot) -> dict[str, str]:
    """선택 상자에 쓸 {표시이름: 코드}."""
    return {
        f"{p['product_name']} {p.get('spec', '')} (재고 {snap.stock(c):,})": c
        for c, p in sorted(snap.products.items(), key=lambda kv: kv[1]["product_name"])
    }


# ---------------------------------------------------------------------
st.title("태오상사 재고")
snap, data = get_snapshot()
st.caption(f"{now_kst():%m월 %d일 %H:%M} 기준 · 상품 {len(snap.products)}개 · 장부 {len(data['stock_ledger']):,}줄")

tab_home, tab_stock, tab_in, tab_sale, tab_order = st.tabs(
    ["대시보드", "재고", "입고", "판매", "발주"]
)

# =====================================================================
# 대시보드
# =====================================================================
with tab_home:
    totals = snap.totals()
    orders = snap.order_list()

    if orders:
        top = orders[0]["product_code"]
        st.subheader(f"지금 발주해야 할 상품 {totals['order']}개")
        st.caption(f"가장 급한 건 {snap.all_products[top]['product_name']} · {fmt_left(snap.days_left(top))}")
    else:
        st.subheader("발주할 것이 없습니다")
        st.caption("모든 품목이 리드타임 안에서 여유가 있습니다.")

    c1, c2 = st.columns(2)
    c1.metric("재고 금액", won(totals["value"]))
    c2.metric("총 재고 수량", f"{totals['units']:,}개")
    c3, c4 = st.columns(2)
    c3.metric("품절", f"{totals['out']}품목")
    c4.metric("오늘 판매", f"{totals['today_sold']:,}개")

    if orders:
        st.markdown("## 소진 임박 순서")
        for p in orders[:10]:
            code = p["product_code"]
            item_row(snap, code, fmt_days(snap.days_left(code)),
                     f"{p.get('spec', '')} · {snap.vendor_of(code).get('vendor_name', '')}")

    issues = snap.issues()
    if issues:
        st.markdown("## 확인이 필요한 것")
        for name, message in issues[:8]:
            st.warning(f"**{name}** — {message}")

    st.markdown("## 최근 장부 기록")
    recent = sorted(
        data["stock_ledger"],
        key=lambda r: (str(r.get("occurred_on", "")), str(r.get("created_at", ""))),
        reverse=True,
    )[:12]
    if recent:
        st.dataframe(
            pd.DataFrame([{
                "날짜": str(r.get("occurred_on", ""))[5:],
                "상품": snap.all_products.get(r["product_code"], {}).get("product_name", r["product_code"]),
                "사유": REASONS.get(r.get("reason", ""), r.get("reason", "")),
                "증감": int(r.get("qty_change", 0)),
            } for r in recent]),
            hide_index=True, use_container_width=True,
        )
    else:
        st.info("아직 기록이 없습니다. 입고 탭에서 초기재고를 넣어 시작하세요.")

    with st.expander("설정과 점검"):
        st.write(f"스프레드시트: [열기](https://docs.google.com/spreadsheets/d/{st.secrets.get('sheet_key', '')})")
        if st.button("장부 다시 읽기", key="reload"):
            refresh()
            st.rerun()
        st.download_button(
            "원장 전체 내려받기 (CSV)",
            pd.DataFrame(data["stock_ledger"]).to_csv(index=False).encode("utf-8-sig"),
            file_name=f"태오상사-원장-{today_kst()}.csv",
            mime="text/csv",
        )
        st.caption(
            "재고 숫자는 원장을 더해서 만듭니다. 스프레드시트에서 숫자를 직접 고치지 마세요. "
            "고쳐야 할 일이 생기면 판매 탭의 재고 조정을 쓰면 이유와 함께 기록이 남습니다."
        )

# =====================================================================
# 재고
# =====================================================================
with tab_stock:
    query = st.text_input("검색", placeholder="상품명, 제조사, 규격", label_visibility="collapsed")
    view = st.radio("보기", ["전체", "보충 필요", "품절", "많이 나가는 순"],
                    horizontal=True, label_visibility="collapsed")

    codes = list(snap.products)
    if query:
        needle = query.lower().split()
        codes = [
            c for c in codes
            if all(
                term in " ".join(str(snap.products[c].get(f, "")) for f in
                                 ("product_name", "maker", "spec", "product_code", "barcode")).lower()
                for term in needle
            )
        ]
    if view == "보충 필요":
        codes = [c for c in codes if snap.health(c) != "ok"]
    elif view == "품절":
        codes = [c for c in codes if snap.stock(c) <= 0]

    if view == "많이 나가는 순":
        codes.sort(key=lambda c: -snap.daily_rate(c))
    else:
        codes.sort(key=lambda c: snap.products[c]["product_name"])

    st.caption(f"{len(codes)}개")
    for code in codes[:120]:
        item_row(snap, code, snap.products[code].get("unit_label", "개"))

    if not codes:
        st.info("해당하는 상품이 없습니다. 검색어를 줄이거나 보기를 전체로 바꿔보세요.")

    st.markdown("## 상품 자세히 보기")
    options = product_options(snap)
    if options:
        picked = st.selectbox("상품", list(options), label_visibility="collapsed")
        code = options[picked]
        p = snap.all_products[code]

        m1, m2, m3 = st.columns(3)
        m1.metric("현재 재고", f"{snap.stock(code):,}")
        m2.metric("안전 재고", f"{p.get('safety_stock', 0)}")
        m3.metric("소진 예상", fmt_days(snap.days_left(code)))

        spike = snap.spike_day(code)
        if spike:
            when, qty = spike
            st.info(
                f"{when:%m월 %d일}에 {qty:,}개가 한꺼번에 나갔습니다. 대량주문으로 보고 "
                f"일평균 계산에서 이 날을 뺐습니다. 앞으로도 이만큼 나갈 거라면 "
                f"안전재고를 올려두세요."
            )

        st.write(
            f"- 최근 7일 {snap.sold7(code):,}개 · 30일 {snap.sold30(code):,}개 · {fmt_rate(snap.daily_rate(code))}\n"
            f"- 매입처 {snap.vendor_of(code).get('vendor_name', '')} · "
            f"리드타임 {snap.vendor_of(code).get('lead_days', 3)}일 · 매입단가 {won(float(p.get('purchase_price') or 0))}\n"
            f"- 권장 발주 "
            + (f"{snap.suggest_qty(code):,}{p.get('unit_label', '개')}" if snap.needs_order(code) else "아직 여유")
        )

        history = snap.history(code, 25)
        if history:
            st.dataframe(
                pd.DataFrame([{
                    "날짜": str(r.get("occurred_on", ""))[5:],
                    "사유": REASONS.get(r.get("reason", ""), r.get("reason", "")),
                    "증감": int(r.get("qty_change", 0)),
                    "거래처/채널": r.get("party", ""),
                    "번호": r.get("ref_no", ""),
                } for r in history]),
                hide_index=True, use_container_width=True,
            )
            st.caption(f"현재 재고 {snap.stock(code):,}는 이 기록을 모두 더한 값입니다.")

        with st.expander("판매처 상품명 연결"):
            existing = [m for m in data["channel_mapping"] if m.get("product_code") == code]
            if existing:
                st.dataframe(
                    pd.DataFrame([{
                        "채널": "쿠팡" if m["channel"] == "COUPANG" else "네이버",
                        "상품명": m.get("channel_name", ""),
                        "옵션": m.get("option_name", ""),
                    } for m in existing]),
                    hide_index=True, use_container_width=True,
                )
            with st.form(f"map_{code}"):
                channel = st.selectbox("판매처", ["COUPANG", "NAVER"],
                                       format_func=lambda x: "쿠팡" if x == "COUPANG" else "네이버 스마트스토어")
                channel_name = st.text_input("판매처에 등록된 상품명")
                option_name = st.text_input("옵션명", placeholder="없으면 비워두세요")
                channel_id = st.text_input("상품번호 또는 옵션ID", placeholder="알면 넣으세요. 가장 확실하게 매칭됩니다")
                if st.form_submit_button("연결 저장") and channel_name.strip():
                    get_store().add_mapping({
                        "product_code": code, "channel": channel,
                        "channel_product_id": channel_id.strip(),
                        "channel_option_id": "",
                        "channel_name": channel_name.strip(),
                        "option_name": option_name.strip(),
                    })
                    refresh()
                    st.success("연결했습니다. 다음 판매 파일부터 자동으로 잡힙니다.")
                    st.rerun()

    with st.expander("상품 추가"):
        with st.form("new_product"):
            name = st.text_input("상품명")
            spec = st.text_input("규격", placeholder="15kg")
            maker = st.text_input("제조사")
            vendors = {v["vendor_name"]: v["vendor_code"] for v in data["vendor"]}
            vendor_name = st.selectbox("매입처", list(vendors) or ["매입처를 먼저 등록하세요"])
            col_a, col_b = st.columns(2)
            price = col_a.number_input("매입단가", min_value=0, step=100)
            safety = col_b.number_input("안전재고", min_value=0, value=5, step=1)
            col_c, col_d = st.columns(2)
            unit = col_c.text_input("단위", value="개")
            order_unit = col_d.number_input("발주 배수", min_value=1, value=1, step=1)
            opening = st.number_input("지금 창고에 있는 수량", min_value=0, step=1)
            st.caption("여기 넣은 수량이 초기재고로 장부 첫 줄에 기록됩니다.")

            if st.form_submit_button("상품 만들기"):
                if not name.strip():
                    st.error("상품명을 넣어주세요.")
                elif not vendors:
                    st.error("매입처를 먼저 등록해야 합니다.")
                else:
                    store = get_store()
                    code = store.next_product_code(data["product"])
                    store.add_product({
                        "product_code": code, "product_name": name.strip(), "spec": spec.strip(),
                        "maker": maker.strip(), "vendor_code": vendors[vendor_name],
                        "barcode": "", "unit_label": unit.strip() or "개",
                        "order_unit": int(order_unit), "purchase_price": int(price),
                        "safety_stock": int(safety),
                    })
                    if opening:
                        store.append_ledger([{
                            "txn_key": f"OPEN|{code}",
                            "product_code": code, "qty_change": int(opening),
                            "reason": "OPENING", "party": "", "ref_no": "상품 등록 실사",
                        }])
                    refresh()
                    st.success(f"{name} 등록했습니다. ({code})")
                    st.rerun()

# =====================================================================
# 입고
# =====================================================================
with tab_in:
    st.session_state.setdefault("basket", [])

    vendors = {v["vendor_name"]: v["vendor_code"] for v in data["vendor"]}
    if not vendors:
        st.warning("매입처가 없습니다. 스프레드시트의 vendor 탭에 먼저 등록하세요.")
    else:
        col_a, col_b = st.columns([3, 2])
        vendor_name = col_a.selectbox("거래처", list(vendors))
        invoice_date = col_b.date_input("거래일자", value=today_kst())
        invoice_no = st.text_input("거래명세표 번호", placeholder="명세표에 인쇄된 번호")
        st.caption("같은 번호로는 두 번 입고되지 않습니다. 명세표를 다시 올려도 재고가 부풀지 않습니다.")

        options = product_options(snap)
        with st.form("add_to_basket", clear_on_submit=True):
            col_c, col_d = st.columns([3, 1])
            picked = col_c.selectbox("상품", list(options), label_visibility="collapsed")
            qty = col_d.number_input("수량", min_value=1, value=1, step=1, label_visibility="collapsed")
            if st.form_submit_button("담기") and options:
                st.session_state.basket.append({"code": options[picked], "qty": int(qty)})

        basket = st.session_state.basket
        if basket:
            st.markdown("## 담은 품목")
            total = 0.0
            for i, line in enumerate(basket):
                p = snap.all_products[line["code"]]
                amount = line["qty"] * float(p.get("purchase_price") or 0)
                total += amount
                col_e, col_f = st.columns([5, 1])
                col_e.write(f"**{p['product_name']}** {p.get('spec', '')} — {line['qty']:,} × {won(float(p.get('purchase_price') or 0))} = {won(amount)}")
                if col_f.button("빼기", key=f"drop{i}"):
                    st.session_state.basket.pop(i)
                    st.rerun()
            st.write(f"### 매입 합계 {won(total)}")

            if st.button("입고 확정", type="primary"):
                if not invoice_no.strip():
                    st.error("거래명세표 번호를 넣어주세요. 중복 입고를 막는 열쇠입니다.")
                else:
                    vendor_code = vendors[vendor_name]
                    entries = [{
                        "txn_key": purchase_key(vendor_code, invoice_no.strip(), i + 1, line["code"]),
                        "product_code": line["code"], "qty_change": line["qty"],
                        "reason": "PURCHASE", "party": vendor_name,
                        "ref_no": invoice_no.strip(), "occurred_on": invoice_date.isoformat(),
                    } for i, line in enumerate(basket)]
                    result = get_store().append_ledger(entries)
                    st.session_state.basket = []
                    refresh()
                    if result["skipped"]:
                        st.warning(f"{result['written']}건 입고 · {result['skipped']}건은 이미 처리된 명세표입니다.")
                    else:
                        st.success(f"{result['written']}건 입고했습니다.")
                    st.rerun()
        else:
            st.info("아직 담은 품목이 없습니다. 위에서 상품과 수량을 골라 담으세요.")

    with st.expander("거래명세표 사진으로 넣기"):
        st.write(
            "명세표를 찍어 Claude 대화창에 올리면 품목과 수량을 표로 뽑아줍니다. "
            "그 표를 머리글째 복사해 아래에 붙여넣으세요."
        )
        st.caption(
            "사진에서 곧바로 재고를 올리지 않는 이유는, 사람 눈으로 한 번 확인한 뒤에만 "
            "장부에 올리기 위해서입니다. 수기 명세표는 특히 잘못 읽힙니다."
        )
        pasted = st.text_area("붙여넣기", height=140, placeholder="상품명\t수량\n백설 하얀설탕 15kg\t10")
        if st.button("표 읽기") and pasted.strip():
            try:
                rows = parsers.parse_invoice_text(pasted)
                matcher = parsers.build_matcher(data["product"], data["channel_mapping"])
                preview = []
                for _, row in rows.iterrows():
                    code, why = matcher("COUPANG", row["raw_name"])
                    preview.append({
                        "명세표 상품명": row["raw_name"], "수량": row["qty"],
                        "매칭": snap.all_products.get(code, {}).get("product_name", "매칭 실패") if code else "매칭 실패",
                        "근거": why,
                    })
                st.dataframe(pd.DataFrame(preview), hide_index=True, use_container_width=True)
                st.caption("맞는지 확인한 뒤 위 담기에서 하나씩 담으세요.")
            except ValueError as exc:
                st.error(str(exc))

# =====================================================================
# 판매
# =====================================================================
with tab_sale:
    channel = st.radio("판매처", ["COUPANG", "NAVER"], horizontal=True,
                       format_func=lambda x: "쿠팡" if x == "COUPANG" else "네이버 스마트스토어")

    uploaded = st.file_uploader("주문 파일", type=["xlsx", "xls", "csv"])
    pasted = st.text_area("또는 엑셀에서 복사해 붙여넣기", height=110,
                          placeholder="머리글 줄까지 함께 복사하세요")

    frame = None
    if uploaded is not None:
        try:
            frame = parsers.read_table(uploaded)
        except Exception as exc:
            st.error(f"파일을 읽지 못했습니다: {exc}")
    elif pasted.strip():
        try:
            frame = parsers.parse_text(pasted)
        except Exception as exc:
            st.error(f"붙여넣은 내용을 읽지 못했습니다: {exc}")

    if frame is not None and not frame.empty:
        columns = list(frame.columns)
        st.caption(f"{len(frame):,}줄을 읽었습니다. 열이 맞는지 확인하세요.")

        guessed = {key: parsers.guess_column(columns, key) for key in
                   ("order", "name", "option", "qty", "status", "date")}
        col_a, col_b = st.columns(2)
        picked = {
            "order": col_a.selectbox("주문번호 열", ["(없음)"] + columns,
                                     index=columns.index(guessed["order"]) + 1 if guessed["order"] else 0),
            "qty": col_b.selectbox("수량 열", ["(없음)"] + columns,
                                   index=columns.index(guessed["qty"]) + 1 if guessed["qty"] else 0),
            "name": col_a.selectbox("상품명 열", ["(없음)"] + columns,
                                    index=columns.index(guessed["name"]) + 1 if guessed["name"] else 0),
            "option": col_b.selectbox("옵션명 열", ["(없음)"] + columns,
                                      index=columns.index(guessed["option"]) + 1 if guessed["option"] else 0),
            "status": col_a.selectbox("주문상태 열", ["(없음)"] + columns,
                                      index=columns.index(guessed["status"]) + 1 if guessed["status"] else 0),
            "date": col_b.selectbox("주문일자 열", ["(없음)"] + columns,
                                    index=columns.index(guessed["date"]) + 1 if guessed["date"] else 0),
        }
        picked = {k: (None if v == "(없음)" else v) for k, v in picked.items()}

        if st.button("확인하기"):
            if not picked["qty"] or not (picked["name"] or picked["option"]):
                st.error("상품명과 수량 열을 골라주세요.")
            else:
                matcher = parsers.build_matcher(data["product"], data["channel_mapping"])
                existing = get_store().existing_txn_keys()
                st.session_state.sale_preview = parsers.parse_sales(
                    frame, channel, picked, matcher, existing, today_kst()
                )

    preview = st.session_state.get("sale_preview")
    if preview is not None and not preview.empty:
        counts = preview["status"].value_counts().to_dict()
        st.markdown("## 확인")
        st.write(
            f"반영 {counts.get('반영', 0)}건 · 중복 {counts.get('중복', 0)}건 · "
            f"매칭 실패 {counts.get('매칭실패', 0)}건 · 취소건 {counts.get('취소건', 0)}건"
        )
        st.dataframe(
            preview[["order_no", "raw_name", "qty", "why", "status"]].rename(columns={
                "order_no": "주문번호", "raw_name": "상품", "qty": "수량",
                "why": "매칭 근거", "status": "처리",
            }),
            hide_index=True, use_container_width=True, height=260,
        )

        failed = preview[preview["status"] == "매칭실패"]
        if not failed.empty:
            st.warning(
                f"{len(failed)}건은 어느 상품인지 알 수 없습니다. 재고 탭에서 그 상품의 "
                "판매처 상품명을 연결하면 다음부터 자동으로 잡힙니다."
            )

        applicable = preview[preview["status"] == "반영"]
        if not applicable.empty and st.button(f"{len(applicable)}건 장부에 반영", type="primary"):
            entries = [{
                "txn_key": row["txn_key"], "product_code": row["product_code"],
                "qty_change": -int(row["qty"]), "reason": channel,
                "party": "쿠팡" if channel == "COUPANG" else "네이버",
                "ref_no": row["order_no"], "occurred_on": row["occurred_on"],
            } for _, row in applicable.iterrows()]
            result = get_store().append_ledger(entries)
            if not failed.empty:
                get_store().log_unmatched([{
                    "channel": channel, "order_no": row["order_no"],
                    "raw_name": row["raw_name"], "qty": int(row["qty"]),
                    "occurred_on": row["occurred_on"], "resolved_code": "",
                } for _, row in failed.iterrows()])
            st.session_state.sale_preview = None
            refresh()
            st.success(f"{result['written']}건 차감했습니다.")
            st.rerun()

    st.markdown("## 손으로 한 건 넣기")
    with st.form("manual_entry"):
        options = product_options(snap)
        picked_name = st.selectbox("상품", list(options))
        reason = st.selectbox(
            "사유",
            ["COUPANG", "NAVER", "RETURN", "CANCEL", "DISPOSE", "DAMAGE", "ADJUST"],
            format_func=lambda r: REASONS[r],
        )
        qty = st.number_input("수량", min_value=0, value=1, step=1)
        memo = st.text_input("메모", placeholder="실사, 유통기한 폐기 등")
        st.caption(
            "재고 조정을 고르면 여기 넣은 수량이 '세어본 실제 수량'이 됩니다. "
            "장부와의 차이만큼만 기록이 남습니다."
        )

        if st.form_submit_button("장부에 기록"):
            code = options[picked_name]
            stamp = now_kst().strftime("%Y%m%d%H%M%S")
            if reason == "ADJUST":
                diff = int(qty) - snap.stock(code)
                if diff == 0:
                    st.info("장부와 같습니다. 기록하지 않았습니다.")
                else:
                    get_store().append_ledger([{
                        "txn_key": manual_key("ADJUST", code, stamp),
                        "product_code": code, "qty_change": diff, "reason": "ADJUST",
                        "party": "실사", "ref_no": memo or "수량 조정",
                    }])
                    refresh()
                    st.success(f"{diff:+,}개 조정했습니다.")
                    st.rerun()
            elif qty > 0:
                sign = -1 if reason in ("COUPANG", "NAVER", "DISPOSE", "DAMAGE") else 1
                get_store().append_ledger([{
                    "txn_key": manual_key(reason, code, stamp),
                    "product_code": code, "qty_change": sign * int(qty), "reason": reason,
                    "party": {"COUPANG": "쿠팡", "NAVER": "네이버"}.get(reason, ""),
                    "ref_no": memo,
                }])
                refresh()
                st.success("장부에 기록했습니다.")
                st.rerun()

# =====================================================================
# 발주
# =====================================================================
with tab_order:
    orders = snap.order_list()
    if not orders:
        st.success("발주할 것이 없습니다. 모든 품목이 리드타임 안에서 여유가 있습니다.")
    else:
        by_vendor: dict[str, list[str]] = {}
        for p in orders:
            by_vendor.setdefault(p.get("vendor_code", ""), []).append(p["product_code"])

        for vendor_code, codes in by_vendor.items():
            vendor = snap.vendors.get(vendor_code, {"vendor_name": vendor_code or "미지정"})
            amount = sum(
                snap.suggest_qty(c) * float(snap.all_products[c].get("purchase_price") or 0)
                for c in codes
            )
            st.markdown(f"## {vendor.get('vendor_name', vendor_code)}")
            st.caption(f"리드타임 {vendor.get('lead_days', 3)}일 · 예상 {won(amount)}")

            for code in codes:
                p = snap.all_products[code]
                item_row(
                    snap, code, f"→ {snap.suggest_qty(code):,}",
                    f"{fmt_rate(snap.daily_rate(code))} · {fmt_left(snap.days_left(code))}",
                )

            minimum = float(vendor.get("min_amount") or 0)
            if minimum and amount < minimum:
                st.warning(f"최소 발주금액 {won(minimum)}에 {won(minimum - amount)} 모자랍니다.")

            def pad(text: str, width: int) -> str:
                # 한글은 고정폭 글꼴에서 두 칸을 차지한다
                used = sum(2 if ord(ch) > 0x2000 else 1 for ch in text)
                return text + " " * max(1, width - used)

            lines = [
                pad(f"{snap.all_products[c]['product_name']} {snap.all_products[c].get('spec', '')}", 34)
                + f"{snap.suggest_qty(c):,} {snap.all_products[c].get('unit_label', '개')}"
                for c in codes
            ]
            slip = "\n".join([
                "태오상사 발주서", f"{today_kst()}",
                f"{vendor.get('vendor_name', vendor_code)} 귀중", "",
                *lines, "", f"합계 {won(amount)}",
            ])
            with st.expander("발주서 만들기"):
                st.code(slip, language=None)
                st.caption(
                    "오른쪽 위 복사 단추를 눌러 카카오톡이나 문자에 붙여넣으세요. "
                    "발주서를 만들어도 재고는 바뀌지 않습니다. 물건이 들어온 날 입고로 기록하세요."
                )
