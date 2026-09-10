"""
태오상사 재고 장부 — Streamlit

두 가지 원칙이 코드 구조를 정한다.

  하나. 현재재고를 어디에도 저장하지 않는다. 원장에 줄을 더해서 만든다.
  둘.  로그인하기 전에는 회사 데이터가 한 글자도 화면에 닿지 않는다.

두 번째를 지키기 위해, 로그인 판정이 끝나기 전에는 어떤 재고 데이터도 읽지 않는다.
require_login() 이 st.stop() 으로 막고, 그 아래 코드는 아예 실행되지 않는다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from lib import audit as audit_lib
from lib import branding, invoice as invoice_lib, parsers, passwords, vision
from lib.auth import (
    DEFAULT_SESSION_HOURS, ROLE_LABEL, User,
    authenticate, build_user_row, failure_message, next_user_id,
    row_to_user, session_alive, session_expiry,
)
from lib.inventory import (
    KIND_LABEL, KIND_OFFSET, KIND_SALE, REASONS, Snapshot,
    boxes_of, fmt_days, fmt_left, fmt_qty, fmt_rate, won,
    manual_key, purchase_key,
)
from lib.sheets import Store, now_kst, today_kst

st.set_page_config(
    page_title="태오상사 재고",
    page_icon="📦",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  /* 상단 메뉴만 감춘다. header 자체를 숨기면 본문이 그 아래로 밀려 들어가
     화면 위가 잘리고, 사이드바 여는 단추까지 사라진다. */
  #MainMenu, footer {visibility:hidden;}
  .block-container {padding:2.4rem 0.9rem 4rem; max-width:640px;}
  h1 {font-size:1.35rem !important; margin-bottom:0.2rem;}
  h2 {font-size:1.1rem !important; margin:1.1rem 0 0.4rem;}
  h3 {font-size:0.98rem !important;}
  .stTabs [data-baseweb="tab-list"] {gap:2px; overflow-x:auto; flex-wrap:nowrap;}
  .stTabs [data-baseweb="tab"] {padding:8px 12px; font-size:0.86rem; white-space:nowrap;}
  .stButton button, .stDownloadButton button {width:100%; min-height:44px; font-weight:600;}
  .stTextInput input, .stNumberInput input, .stDateInput input {min-height:42px; font-size:16px;}
  [data-testid="stMetricValue"] {font-size:1.5rem;}
  [data-testid="stMetricLabel"] {font-size:0.78rem;}
  .stDataFrame {font-size:0.8rem;}
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
  .login-wrap {max-width:340px; margin:7vh auto 0;}
  .login-wrap svg, .login-wrap img {margin:0 auto;}
  .login-wrap h1 {text-align:center; margin-bottom:0.1rem;}
  .login-wrap p.sub {text-align:center; opacity:.6; font-size:0.85rem; margin-bottom:1.4rem;}
</style>
""", unsafe_allow_html=True)


# =====================================================================
# 연결
# =====================================================================
@st.cache_resource(show_spinner=False)
def get_store() -> Store:
    if "gcp_service_account" not in st.secrets or "sheet_key" not in st.secrets:
        st.error(
            "설정이 없습니다. Streamlit Cloud의 Settings → Secrets 에 "
            "`sheet_key` 와 `[gcp_service_account]` 를 넣어주세요."
        )
        st.stop()
    return Store(dict(st.secrets["gcp_service_account"]), st.secrets["sheet_key"])


@st.cache_data(ttl=180, show_spinner="장부를 읽는 중")
def load_data(_version: int) -> dict:
    return get_store().read_all()


def refresh() -> None:
    st.session_state.data_version = st.session_state.get("data_version", 0) + 1


def get_snapshot() -> tuple[Snapshot, dict]:
    data = load_data(st.session_state.get("data_version", 0))
    return Snapshot(data["product"], data["vendor"], data["stock_ledger"], today=today_kst()), data


def client_hint() -> str:
    """접속 단서. 브라우저 종류 정도만 남긴다. 정확한 IP는 얻을 수 없다."""
    try:
        return str(st.context.headers.get("User-Agent", ""))[:80]
    except Exception:
        return ""


# =====================================================================
# 로그인
# =====================================================================
def bootstrap_admin_if_needed(store: Store) -> str | None:
    """
    사용자가 한 명도 없을 때, Secrets에 적어둔 초기 관리자를 만든다.

    아이디와 비밀번호가 코드나 GitHub에 남지 않는다. Secrets에만 있고,
    계정을 만든 뒤에는 그 항목을 지우면 된다.
    """
    if store.read_users():
        return None
    if "initial_admin" not in st.secrets:
        return "NO_SECRET"

    conf = st.secrets["initial_admin"]
    login_id = str(conf.get("login_id", "")).strip().lower()
    password = str(conf.get("password", ""))
    name = str(conf.get("user_name", "관리자"))
    if not login_id or not password:
        return "NO_SECRET"

    store.add_user(build_user_row(next_user_id([]), login_id, name, "ADMIN", password))
    store.append_audit([audit_lib.entry(
        User(user_id="", login_id="system", user_name="시스템", role="ADMIN"),
        "USER_CREATE", note=f"초기 관리자 {login_id} 생성",
    )])
    return "CREATED"


def render_login() -> None:
    st.markdown("<div class='login-wrap'>", unsafe_allow_html=True)
    st.markdown(branding.logo_markup(), unsafe_allow_html=True)
    st.markdown("<h1>태오상사</h1><p class='sub'>재고관리 시스템</p>", unsafe_allow_html=True)

    store = get_store()
    try:
        state = bootstrap_admin_if_needed(store)
    except Exception as exc:
        st.error(f"스프레드시트에 연결하지 못했습니다: {exc}")
        st.stop()

    if state == "CREATED":
        st.success("초기 관리자 계정을 만들었습니다. Secrets의 `[initial_admin]` 은 이제 지우셔도 됩니다.")
    elif state == "NO_SECRET":
        st.warning(
            "등록된 사용자가 없습니다. Streamlit Secrets에 아래를 넣고 새로고침하세요.\n\n"
            "```toml\n[initial_admin]\nlogin_id = \"taeo_admin\"\n"
            "password = \"충분히 긴 비밀번호\"\nuser_name = \"이혜균\"\n```"
        )

    with st.form("login"):
        login_id = st.text_input("아이디")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인", type="primary")

    if submitted:
        result = authenticate(store.read_users(), store.read_login_log(), login_id, password)
        if result.ok:
            hours = int(st.secrets.get("session_hours", DEFAULT_SESSION_HOURS))
            st.session_state.user = result.user
            st.session_state.expires_at = session_expiry(hours).isoformat()
            store.append_login(result.user.login_id, "SUCCESS", result.user.user_name, client_hint())
            store.update_user(result.user.user_id,
                              {"last_login_at": now_kst().isoformat(timespec="seconds")})
            st.rerun()
        else:
            store.append_login((login_id or "").strip().lower(), "FAIL", "",
                               client_hint(), result.reason)
            st.error(failure_message(result))

    st.markdown("</div>", unsafe_allow_html=True)


def require_login() -> User:
    """로그인하지 않았으면 여기서 멈춘다. 아래 코드는 실행되지 않는다."""
    current = st.session_state.get("user")
    if current is not None and session_alive(st.session_state.get("expires_at")):
        return current
    if current is not None:
        st.session_state.pop("user", None)
        st.warning("로그인이 만료되었습니다. 다시 로그인해주세요.")
    render_login()
    st.stop()


# =====================================================================
# 공통 조각
# =====================================================================
def item_row(snap: Snapshot, code: str, right: str, sub: str | None = None) -> None:
    p = snap.all_products.get(code, {})
    h = snap.health(code)
    meta = sub if sub is not None else f"{p.get('spec', '')} · {p.get('maker', '')}"
    if snap.is_offset(code):
        meta = "상계용 · " + meta
    # 박스로 받는 물건은 낱개 옆에 박스 수를 함께 보여준다
    boxes = boxes_of(snap.stock(code), snap.per_box(code))
    if boxes:
        right = f"{boxes} · {right}"
    st.markdown(
        f"<div class='item'><div class='bar {h}'></div>"
        f"<div class='txt'><div class='nm'>{p.get('product_name', code)}</div>"
        f"<div class='mt'>{meta}</div></div>"
        f"<div class='qt {h}'>{snap.stock(code):,}<small>{right}</small></div></div>",
        unsafe_allow_html=True,
    )


def product_options(snap: Snapshot, codes: list[str] | None = None) -> dict[str, str]:
    pool = codes if codes is not None else list(snap.products)
    return {
        f"{snap.products[c]['product_name']} {snap.products[c].get('spec', '')} "
        f"(재고 {fmt_qty(snap.stock(c), snap.per_box(c), snap.products[c].get('unit_label', '개'))})": c
        for c in sorted(pool, key=lambda x: snap.products[x]["product_name"])
    }


def write_ledger(entries: list[dict], actor: User, audit_rows: list[dict] | None = None) -> dict:
    """원장에 쓰고, 필요하면 작업 기록을 함께 남긴다."""
    store = get_store()
    result = store.append_ledger(entries, created_by=actor.login_id)
    if audit_rows:
        store.append_audit(audit_rows)
    refresh()
    return result


# =====================================================================
# 대시보드
# =====================================================================
def page_dashboard(snap: Snapshot, data: dict, actor: User) -> None:
    totals = snap.totals()                       # 판매용만
    offset_codes = snap.offset_codes()
    orders = snap.order_list()

    if orders:
        top = orders[0]["product_code"]
        st.subheader(f"지금 발주해야 할 상품 {totals['order']}개")
        st.caption(f"가장 급한 건 {snap.all_products[top]['product_name']} · {fmt_left(snap.days_left(top))}")
    else:
        st.subheader("발주할 것이 없습니다")
        st.caption("모든 품목이 리드타임 안에서 여유가 있습니다.")

    c1, c2 = st.columns(2)
    if actor.can("price.view"):
        c1.metric("재고 금액", won(totals["value"]))
    else:
        c1.metric("총 상품", f"{totals['products']}개")
    c2.metric("총 재고 수량", f"{totals['units']:,}개")
    c3, c4 = st.columns(2)
    c3.metric("품절", f"{totals['out']}품목")
    c4.metric("오늘 판매", f"{totals['today_sold']:,}개")
    st.caption("판매용 품목 기준입니다. 상계용은 아래에 따로 있습니다.")

    if offset_codes:
        off = snap.totals(offset_codes)
        st.markdown("## 상계용 품목")
        o1, o2 = st.columns(2)
        if actor.can("price.view"):
            o1.metric("상계 재고 금액", won(off["value"]))
        else:
            o1.metric("상계 품목", f"{off['products']}개")
        o2.metric("상계 재고 수량", f"{off['units']:,}개")
        for code in sorted(offset_codes,
                           key=lambda c: snap.all_products[c]["product_name"])[:8]:
            item_row(snap, code, snap.all_products[code].get("unit_label", "개"))
        st.caption("거래처와 오가는 물건입니다. 발주 계산과 재고금액에서 따로 셉니다.")

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
                "처리자": r.get("created_by", ""),
            } for r in recent]),
            hide_index=True, use_container_width=True,
        )
    else:
        st.info("아직 기록이 없습니다. 재고 탭에서 상품과 초기재고를 넣어 시작하세요.")

    with st.expander("설정과 점검"):
        if st.button("장부 다시 읽기", key="reload"):
            refresh()
            st.rerun()
        if actor.can("audit.view"):
            st.download_button(
                "원장 전체 내려받기 (CSV)",
                pd.DataFrame(data["stock_ledger"]).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"태오상사-원장-{today_kst()}.csv", mime="text/csv",
            )
        st.caption(
            "재고 숫자는 원장을 더해서 만듭니다. 스프레드시트에서 숫자를 직접 고치지 마세요. "
            "고쳐야 할 일이 생기면 판매 탭의 재고 조정을 쓰면 이유와 처리자가 함께 남습니다."
        )


# =====================================================================
# 재고
# =====================================================================
def page_stock(snap: Snapshot, data: dict, actor: User) -> None:
    query = st.text_input("검색", placeholder="상품명, 제조사, 규격", label_visibility="collapsed")
    view = st.radio("보기", ["판매용", "상계용", "보충 필요", "품절", "전체"],
                    horizontal=True, label_visibility="collapsed")

    codes = list(snap.products)
    if view == "판매용":
        codes = snap.sale_codes()
    elif view == "상계용":
        codes = snap.offset_codes()
        if not codes:
            st.info("상계용으로 지정된 품목이 없습니다. 상품정보 수정에서 구분을 바꿀 수 있습니다.")
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
        codes = [c for c in snap.sale_codes() if snap.health(c) != "ok"]
    elif view == "품절":
        codes = [c for c in codes if snap.stock(c) <= 0]
    codes.sort(key=lambda c: snap.products[c]["product_name"])

    st.caption(f"{len(codes)}개")
    for code in codes[:120]:
        item_row(snap, code, snap.products[code].get("unit_label", "개"))
    if not codes:
        st.info("해당하는 상품이 없습니다. 검색어를 줄이거나 보기를 전체로 바꿔보세요.")

    st.markdown("## 상품 자세히 보기")
    options = product_options(snap)
    if not options:
        if actor.can("product.create"):
            _product_create_form(data, actor)
        else:
            st.info("등록된 상품이 없습니다.")
        return

    picked = st.selectbox("상품", list(options), label_visibility="collapsed")
    code = options[picked]
    p = snap.all_products[code]

    per_box = snap.per_box(code)
    unit = p.get("unit_label", "개")
    m1, m2, m3 = st.columns(3)
    m1.metric("현재 재고", f"{snap.stock(code):,}",
              boxes_of(snap.stock(code), per_box) or None)
    m2.metric("안전 재고", f"{p.get('safety_stock', 0)}")
    if snap.is_offset(code):
        m3.metric("구분", "상계용")
    else:
        m3.metric("소진 예상", fmt_days(snap.days_left(code)))

    if snap.is_offset(code):
        st.info(
            "거래처와 오가는 상계용 품목입니다. 발주 계산과 대시보드 재고금액에서 "
            "판매용과 따로 셉니다."
        )

    spike = snap.spike_day(code)
    if spike:
        when, qty = spike
        st.info(
            f"{when:%m월 %d일}에 {qty:,}개가 한꺼번에 나갔습니다. 대량주문으로 보고 "
            "일평균 계산에서 이 날을 뺐습니다. 앞으로도 이만큼 나갈 거라면 안전재고를 올려두세요."
        )

    lines = [f"- 재고 {fmt_qty(snap.stock(code), per_box, unit)}"]
    if per_box > 1:
        lines.append(f"- 한 박스에 {per_box}{unit}")
    if not snap.is_offset(code):
        lines.append(
            f"- 최근 7일 {snap.sold7(code):,}개 · 30일 {snap.sold30(code):,}개 · "
            f"{fmt_rate(snap.daily_rate(code))}")
    lines.append(f"- 매입처 {snap.vendor_of(code).get('vendor_name', '')} · "
                 f"리드타임 {snap.vendor_of(code).get('lead_days', 3)}일")
    if actor.can("price.view"):
        lines.append(f"- 매입단가 {won(float(p.get('purchase_price') or 0))}")
    if not snap.is_offset(code):
        lines.append("- 권장 발주 " + (fmt_qty(snap.suggest_qty(code), per_box, unit)
                                    if snap.needs_order(code) else "아직 여유"))
    st.write("\n".join(lines))

    history = snap.history(code, 25)
    if history:
        st.dataframe(
            pd.DataFrame([{
                "날짜": str(r.get("occurred_on", ""))[5:],
                "사유": REASONS.get(r.get("reason", ""), r.get("reason", "")),
                "증감": int(r.get("qty_change", 0)),
                "거래처/채널": r.get("party", ""),
                "처리자": r.get("created_by", ""),
            } for r in history]),
            hide_index=True, use_container_width=True,
        )
        st.caption(f"현재 재고 {snap.stock(code):,}는 이 기록을 모두 더한 값입니다.")

    if actor.can("product.edit"):
        _product_edit_form(code, p, actor)
    if actor.can("mapping.edit"):
        _mapping_form(code, data, actor)
    if actor.can("product.create"):
        _product_create_form(data, actor)


def _product_edit_form(code: str, p: dict, actor: User) -> None:
    """상품정보 수정. 바뀐 항목마다 변경 전후 값을 기록에 남긴다."""
    with st.expander("상품정보 수정"):
        with st.form(f"edit_{code}"):
            name = st.text_input("상품명", value=str(p.get("product_name", "")))
            spec = st.text_input("규격", value=str(p.get("spec", "")))
            col_a, col_b = st.columns(2)
            price = col_a.number_input("매입단가", min_value=0, step=100,
                                       value=int(float(p.get("purchase_price") or 0)))
            safety = col_b.number_input("안전재고", min_value=0, step=1,
                                        value=int(float(p.get("safety_stock") or 0)))
            col_c, col_d = st.columns(2)
            per_box = col_c.number_input(
                "한 박스 입수", min_value=1, step=1,
                value=max(1, int(float(p.get("units_per_box") or 1))))
            order_unit = col_d.number_input("발주 배수", min_value=1, step=1,
                                            value=max(1, int(float(p.get("order_unit") or 1))))
            kind = st.radio(
                "품목 구분", [KIND_SALE, KIND_OFFSET],
                index=0 if str(p.get("item_kind", "")).upper() != KIND_OFFSET else 1,
                format_func=lambda k: KIND_LABEL[k], horizontal=True)
            st.caption(
                "상계용은 거래처와 오가는 물건입니다. 온라인 판매 기록이 없으므로 "
                "발주 계산과 대시보드 재고금액에서 따로 셉니다."
            )
            barcode = st.text_input("거래처 상품코드", value=str(p.get("barcode", "")),
                                    placeholder="거래처가 명세표에 찍는 코드가 있으면")
            active = st.checkbox("판매 중", value=str(p.get("is_active", "TRUE")).upper() != "FALSE")
            st.caption("단종된 상품은 지우지 말고 '판매 중'을 해제하세요. 과거 기록이 남습니다.")

            if not st.form_submit_button("수정 저장"):
                return

            candidates = {
                "product_name": (str(p.get("product_name", "")), name.strip()),
                "spec": (str(p.get("spec", "")), spec.strip()),
                "purchase_price": (str(int(float(p.get("purchase_price") or 0))), str(int(price))),
                "safety_stock": (str(int(float(p.get("safety_stock") or 0))), str(int(safety))),
                "barcode": (str(p.get("barcode", "")), barcode.strip()),
                "order_unit": (str(int(float(p.get("order_unit") or 1))), str(int(order_unit))),
                "units_per_box": (str(int(float(p.get("units_per_box") or 1))), str(int(per_box))),
                "item_kind": (str(p.get("item_kind", "") or KIND_SALE).upper(), kind),
                "is_active": (str(p.get("is_active", "TRUE")).upper(), "TRUE" if active else "FALSE"),
            }
            changed = {f: after for f, (before, after) in candidates.items() if before != after}
            if not changed:
                st.info("바뀐 것이 없습니다.")
                return

            store = get_store()
            store.update_product(code, changed)
            store.append_audit([
                audit_lib.entry(actor, "PRODUCT_EDIT", feature=field, product_code=code,
                                before=candidates[field][0], after=candidates[field][1])
                for field in changed
            ])
            refresh()
            st.success(f"{len(changed)}개 항목을 고쳤습니다.")
            st.rerun()


def _mapping_form(code: str, data: dict, actor: User) -> None:
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
            channel_id = st.text_input("상품번호 또는 옵션ID",
                                       placeholder="알면 넣으세요. 가장 확실하게 매칭됩니다")
            if st.form_submit_button("연결 저장") and channel_name.strip():
                store = get_store()
                store.add_mapping({
                    "product_code": code, "channel": channel,
                    "channel_product_id": channel_id.strip(), "channel_option_id": "",
                    "channel_name": channel_name.strip(), "option_name": option_name.strip(),
                })
                store.append_audit([audit_lib.entry(
                    actor, "MAPPING_ADD", product_code=code,
                    after=f"{channel} / {channel_name.strip()}", note=option_name.strip(),
                )])
                refresh()
                st.success("연결했습니다. 다음 판매 파일부터 자동으로 잡힙니다.")
                st.rerun()


def _product_create_form(data: dict, actor: User) -> None:
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
            unit = col_c.text_input("낱개 단위", value="개")
            per_box = col_d.number_input("한 박스 입수", min_value=1, value=1, step=1)
            order_unit = st.number_input("발주 배수", min_value=1, value=1, step=1)
            kind = st.radio("품목 구분", [KIND_SALE, KIND_OFFSET],
                            format_func=lambda k: KIND_LABEL[k], horizontal=True)
            opening = st.number_input("지금 창고에 있는 낱개 수량", min_value=0, step=1)
            st.caption(
                "재고는 언제나 낱개로 셉니다. 박스는 입수로 환산해 함께 보여줍니다. "
                "여기 넣은 수량이 초기재고로 장부 첫 줄에 기록됩니다."
            )

            if not st.form_submit_button("상품 만들기"):
                return
            if not name.strip():
                st.error("상품명을 넣어주세요.")
                return
            if not vendors:
                st.error("매입처를 먼저 등록해야 합니다.")
                return

            store = get_store()
            code = store.next_product_code(data["product"])
            store.add_product({
                "product_code": code, "product_name": name.strip(), "spec": spec.strip(),
                "maker": maker.strip(), "vendor_code": vendors[vendor_name],
                "barcode": "", "unit_label": unit.strip() or "개",
                "order_unit": int(order_unit), "purchase_price": int(price),
                "safety_stock": int(safety),
                "units_per_box": int(per_box), "item_kind": kind,
            })
            audit_rows = [audit_lib.entry(actor, "PRODUCT_CREATE", product_code=code,
                                          after=name.strip())]
            if opening:
                audit_rows.append(audit_lib.entry(
                    actor, "STOCK_ADJUST", feature="초기재고", product_code=code,
                    before=0, after=int(opening), qty_change=int(opening),
                    note="상품 등록 시 실사",
                ))
                write_ledger([{
                    "txn_key": f"OPEN|{code}", "product_code": code,
                    "qty_change": int(opening), "reason": "OPENING",
                    "party": "", "ref_no": "상품 등록 실사",
                }], actor, audit_rows)
            else:
                store.append_audit(audit_rows)
                refresh()
            st.success(f"{name} 등록했습니다. ({code})")
            st.rerun()


# =====================================================================
# 입고
# =====================================================================
def page_inbound(snap: Snapshot, data: dict, actor: User) -> None:
    vendors = {v["vendor_name"]: v["vendor_code"] for v in data["vendor"]}
    if not vendors:
        st.warning("매입처가 없습니다. 스프레드시트의 vendor 탭에 먼저 등록하세요.")
        return

    mode = st.radio(
        "입고 방법", ["거래명세표 촬영", "손으로 담기"],
        horizontal=True, label_visibility="collapsed",
    )
    if mode == "거래명세표 촬영":
        _invoice_flow(snap, data, actor, vendors)
        return

    _manual_inbound(snap, data, actor, vendors)


def _manual_inbound(snap: Snapshot, data: dict, actor: User, vendors: dict) -> None:
    """손으로 담는 기존 방식. 사진이 잘 안 읽힐 때 쓴다."""
    st.session_state.setdefault("basket", [])

    col_a, col_b = st.columns([3, 2])
    vendor_name = col_a.selectbox("거래처", list(vendors))
    invoice_date = col_b.date_input("거래일자", value=today_kst())
    invoice_no = st.text_input("거래명세표 번호", placeholder="명세표에 인쇄된 번호")
    st.caption("같은 번호로는 두 번 입고되지 않습니다. 명세표를 다시 올려도 재고가 부풀지 않습니다.")

    options = product_options(snap)
    if not options:
        st.info("등록된 상품이 없습니다. 재고 탭에서 먼저 상품을 만드세요.")
        return

    with st.form("add_to_basket", clear_on_submit=True):
        col_c, col_d = st.columns([3, 1])
        picked = col_c.selectbox("상품", list(options), label_visibility="collapsed")
        qty = col_d.number_input("수량", min_value=1, value=1, step=1, label_visibility="collapsed")
        if st.form_submit_button("담기"):
            st.session_state.basket.append({"code": options[picked], "qty": int(qty)})

    basket = st.session_state.basket
    if not basket:
        st.info("아직 담은 품목이 없습니다. 위에서 상품과 수량을 골라 담으세요.")
    else:
        st.markdown("## 담은 품목")
        total = 0.0
        for i, line in enumerate(basket):
            p = snap.all_products[line["code"]]
            amount = line["qty"] * float(p.get("purchase_price") or 0)
            total += amount
            col_e, col_f = st.columns([5, 1])
            label = f"**{p['product_name']}** {p.get('spec', '')} — {line['qty']:,}"
            if actor.can("price.view"):
                label += f" × {won(float(p.get('purchase_price') or 0))} = {won(amount)}"
            col_e.write(label)
            if col_f.button("빼기", key=f"drop{i}"):
                st.session_state.basket.pop(i)
                st.rerun()
        if actor.can("price.view"):
            st.write(f"### 매입 합계 {won(total)}")

        if st.button("입고 확정", type="primary"):
            if not invoice_no.strip():
                st.error("거래명세표 번호를 넣어주세요. 중복 입고를 막는 열쇠입니다.")
            else:
                vendor_code = vendors[vendor_name]
                entries, audits = [], []
                for i, line in enumerate(basket):
                    before = snap.stock(line["code"])
                    entries.append({
                        "txn_key": purchase_key(vendor_code, invoice_no.strip(), i + 1, line["code"]),
                        "product_code": line["code"], "qty_change": line["qty"],
                        "reason": "PURCHASE", "party": vendor_name,
                        "ref_no": invoice_no.strip(), "occurred_on": invoice_date.isoformat(),
                    })
                    audits.append(audit_lib.entry(
                        actor, "INBOUND", product_code=line["code"],
                        before=before, after=before + line["qty"], qty_change=line["qty"],
                        ref_no=invoice_no.strip(), note=vendor_name,
                    ))
                result = write_ledger(entries, actor, audits)
                st.session_state.basket = []
                if result["skipped"]:
                    st.warning(f"{result['written']}건 입고 · {result['skipped']}건은 이미 처리된 명세표입니다.")
                else:
                    st.success(f"{result['written']}건 입고했습니다.")
                st.rerun()


# ---------------------------------------------------------------------
# 거래명세표 촬영 입고
# ---------------------------------------------------------------------
def _invoice_flow(snap: Snapshot, data: dict, actor: User, vendors: dict) -> None:
    """
    찍는다 → 읽는다 → 확인한다 → 확정한다.

    사진을 찍었다고 재고가 움직이지 않는다. 사람이 화면에서 보고
    입고 확정을 눌러야 원장에 들어간다.
    """
    draft = st.session_state.get("invoice_draft")

    if draft is None:
        _invoice_capture(data, actor)
        return

    _invoice_review(snap, data, actor, vendors, draft)


def _invoice_capture(data: dict, actor: User) -> None:
    source = st.radio("촬영 방법", ["카메라로 촬영", "사진 올리기"],
                      horizontal=True, label_visibility="collapsed")

    shots: list[bytes] = []
    if source == "카메라로 촬영":
        shot = st.camera_input("거래명세표를 화면에 꽉 채워 찍으세요")
        if shot is not None:
            shots = [shot.getvalue()]
        st.caption(
            "명세표 전체가 들어오고 글자가 또렷하게 나오도록 찍으세요. "
            "그늘에서 찍으면 그림자 없이 잘 나옵니다."
        )
    else:
        files = st.file_uploader("거래명세표 사진", type=["jpg", "jpeg", "png"],
                                 accept_multiple_files=True)
        if files:
            shots = [f.getvalue() for f in files]
            columns = st.columns(min(len(shots), 3))
            for column, raw in zip(columns, shots[:3]):
                column.image(raw, use_container_width=True)
        st.caption("한 명세표가 여러 장에 걸쳐 있으면 함께 올리세요. 한 건으로 읽습니다.")

    if not shots:
        return

    if st.button(f"{len(shots)}장 분석하기", type="primary"):
        key = st.secrets.get("anthropic_api_key", "")
        model = st.secrets.get("claude_model", vision.DEFAULT_MODEL)
        with st.spinner("거래명세표를 분석하고 있습니다"):
            try:
                read = vision.analyze(shots, key, model)
            except ValueError as exc:
                st.error(str(exc))
                return

        if not read.items:
            st.error(
                "명세표에서 상품을 찾지 못했습니다. "
                + (read.note or "더 밝은 곳에서 명세표 전체가 들어오게 다시 찍어보세요.")
            )
            return

        st.session_state.invoice_draft = invoice_lib.build_draft(
            read, data["product"], data["vendor"],
            get_store().read_aliases(), data["channel_mapping"],
            fallback_date=today_kst(),
        )
        st.session_state.invoice_shots = len(shots)
        st.rerun()


def _invoice_review(snap: Snapshot, data: dict, actor: User,
                    vendors: dict, draft) -> None:
    if st.button("다시 촬영"):
        for key in ("invoice_draft", "invoice_shots"):
            st.session_state.pop(key, None)
        st.rerun()

    # ----- 기본 정보. 잘못 읽었으면 여기서 고친다 -----
    names = list(vendors)
    index = 0
    for i, name in enumerate(names):
        if vendors[name] == draft.vendor_code:
            index = i
            break
    if not draft.vendor_code:
        st.warning(
            f"명세표의 거래처를 '{draft.vendor_name or '읽지 못함'}'으로 읽었지만 "
            "등록된 매입처와 맞지 않습니다. 아래에서 골라주세요."
        )

    col_a, col_b = st.columns([3, 2])
    vendor_name = col_a.selectbox("거래처", names, index=index)
    invoice_date = col_b.date_input(
        "거래일자", value=date.fromisoformat(draft.invoice_date))
    invoice_no = st.text_input("거래명세표 번호", value=draft.invoice_no)

    if draft.invoice_no_generated:
        st.caption(
            "명세표에 번호가 없어 시스템이 만든 번호입니다. 같은 사진을 다시 찍어도 "
            "같은 번호가 나오므로 중복 입고를 막습니다."
        )

    if draft.is_return:
        st.error(
            "**반품 명세표입니다.** 수량이 모두 음수로 찍혀 있습니다. "
            "확정하면 재고가 그만큼 **줄어듭니다.** 물건을 실제로 거래처에 돌려보냈는지 "
            "확인하고 진행하세요."
        )
    elif draft.mixed:
        st.warning(
            f"받은 물건 {len(draft.inbound_lines)}줄과 돌려보낸 물건 "
            f"{len(draft.return_lines)}줄이 한 장에 섞여 있습니다. "
            "부호를 줄마다 확인하세요."
        )

    low = sum(1 for line in draft.lines if line.confidence == "low")
    if low:
        st.warning(f"{low}줄은 글자가 흐려 확신이 낮습니다. 수량을 특히 잘 확인하세요.")
    if draft.note:
        st.caption(f"분석 메모: {draft.note}")

    # ----- 품목 확인 -----
    st.markdown("## 읽은 품목")
    labels = {c: f"{p['product_name']} {p.get('spec', '')}".strip()
              for c, p in snap.products.items()}
    choices = [invoice_lib.UNMATCHED] + sorted(labels.values())
    by_label = {v: k for k, v in labels.items()}

    table = pd.DataFrame([{
        "명세표 상품": line.raw_name,
        "수량": line.qty,
        "시스템 상품": labels.get(line.product_code, invoice_lib.UNMATCHED),
        "상태": line.status + ("  (흐림)" if line.confidence == "low" else ""),
    } for line in draft.lines])

    edited = st.data_editor(
        table,
        hide_index=True, use_container_width=True, num_rows="fixed",
        column_config={
            "명세표 상품": st.column_config.TextColumn(disabled=True, width="medium"),
            # 하한을 두지 않는다. 음수는 반품이라 정상적인 값이다.
            "수량": st.column_config.NumberColumn(step=1, width="small", format="%d"),
            "시스템 상품": st.column_config.SelectboxColumn(options=choices, width="medium"),
            "상태": st.column_config.TextColumn(disabled=True, width="small"),
        },
        key="invoice_editor",
    )
    st.caption("수량 앞의 마이너스는 거래처로 돌려보낸 것입니다. 그대로 두면 재고가 줄어듭니다.")

    # 화면에서 고친 내용을 초안에 되돌린다
    for line, (_, row) in zip(draft.lines, edited.iterrows()):
        line.qty = int(row["수량"] or 0)
        picked = by_label.get(row["시스템 상품"], "")
        if picked != line.product_code:
            line.product_code = picked
            line.basis = "사용자 확정" if picked else ""

    draft.vendor_code = vendors[vendor_name]
    draft.vendor_name = vendor_name
    draft.invoice_date = invoice_date.isoformat()
    draft.invoice_no = invoice_no.strip()

    # 명세표의 박스 칸과 총수량이 다르면 입수를 알 수 있다.
    # 상품 마스터에 아직 없는 값이면 채워 넣을지 물어본다.
    learnable = []
    for line in draft.ready_lines:
        found = line.per_box
        if found and found > 1 and snap.per_box(line.product_code) != found:
            learnable.append((line, found))
    if learnable:
        with st.expander(f"박스 입수 {len(learnable)}건을 상품에 저장할까요"):
            for line, found in learnable:
                st.write(f"- **{labels.get(line.product_code, line.product_code)}** — "
                         f"한 박스에 {found}{snap.all_products[line.product_code].get('unit_label', '개')}")
            st.caption("저장하면 앞으로 재고를 낱개와 박스로 함께 보여줍니다.")
            save_boxes = st.checkbox("입수 저장", value=True, key="save_per_box")
    else:
        save_boxes = False

    ready = draft.ready_lines
    total_qty = sum(line.qty for line in ready)
    in_qty = sum(line.qty for line in draft.inbound_lines)
    out_qty = sum(-line.qty for line in draft.return_lines)

    if draft.pending_count:
        st.warning(
            f"{draft.pending_count}줄이 아직 어느 상품인지 정해지지 않았습니다. "
            "시스템 상품을 골라주거나, 새 상품이면 재고 탭에서 먼저 등록하세요. "
            "정해지지 않은 줄은 입고되지 않습니다."
        )
        for line in draft.lines:
            if line.ready or not line.candidates:
                continue
            hints = ", ".join(f"{labels.get(c, c)} ({score:.0%})"
                              for c, score in line.candidates[:3])
            st.caption(f"**{line.raw_name}** — 비슷한 상품: {hints}")

    if not ready:
        st.info("입고할 품목이 없습니다.")
        return

    # 돌려보낼 수량이 재고보다 많으면 재고가 음수로 떨어진다
    short = draft.shortfall(snap.stock)
    if short:
        for line, have in short:
            st.error(
                f"**{labels.get(line.product_code, line.product_code)}** — "
                f"돌려보낼 수량이 현재 재고({have:,}개)보다 많습니다. "
                "수량을 확인하거나, 먼저 입고가 빠졌는지 보세요."
            )

    with st.expander("품목별 낱개·박스 확인"):
        for line in ready:
            box = snap.per_box(line.product_code)
            st.write(f"- {labels.get(line.product_code, line.raw_name)} — "
                     f"{fmt_qty(line.qty, box, snap.all_products[line.product_code].get('unit_label', '개'))}")

    if draft.mixed:
        st.write(f"### 입고 {in_qty:,}개 · 반품 {out_qty:,}개 · {len(ready)}품목")
        label = "입고·반품 확정"
    elif draft.is_return:
        st.write(f"### 반품 예정 {len(ready)}품목 · 총 {out_qty:,}개")
        label = "반품 확정 (재고가 줄어듭니다)"
    else:
        st.write(f"### 입고 예정 {len(ready)}품목 · 총 {total_qty:,}개")
        label = "입고 확정"

    if draft.return_lines:
        confirmed = st.checkbox(
            f"{out_qty:,}개를 거래처로 돌려보낸 것이 맞습니다", key="return_confirm")
        if not confirmed:
            st.caption("반품은 재고를 줄이는 작업이라 한 번 더 확인합니다.")

    if not st.button(label, type="primary"):
        return

    if draft.return_lines and not st.session_state.get("return_confirm"):
        st.error("반품 확인란에 체크해주세요.")
        return
    if short:
        st.error("재고보다 많이 돌려보낼 수는 없습니다. 수량을 고쳐주세요.")
        return

    if not draft.invoice_no:
        st.error("거래명세표 번호를 넣어주세요. 중복 입고를 막는 열쇠입니다.")
        return

    store = get_store()
    if invoice_lib.already_processed(draft, store.existing_txn_keys()):
        st.error("이미 입고 처리된 거래명세표입니다. 재고를 다시 반영하지 않았습니다.")
        return

    audits = []
    for line in ready:
        before = snap.stock(line.product_code)
        audits.append(audit_lib.entry(
            actor, "RETURN_OUT" if line.is_return else "INBOUND",
            product_code=line.product_code,
            before=before, after=before + line.qty, qty_change=line.qty,
            ref_no=draft.invoice_no,
            note=f"{vendor_name} · 명세표 촬영" + ("  반품" if line.is_return else ""),
        ))
    audits.append(audit_lib.entry(
        actor, "INVOICE_RETURN" if draft.is_return else "INVOICE_SCAN",
        feature="거래명세표 반품" if draft.is_return else "거래명세표 입고",
        qty_change=total_qty, ref_no=draft.invoice_no,
        note=f"{vendor_name} · 입고 {in_qty}개 / 반품 {out_qty}개"
             if draft.mixed else f"{vendor_name} · {len(ready)}품목 / 총 {abs(total_qty)}개",
    ))

    result = write_ledger(invoice_lib.to_entries(draft), actor, audits)

    fresh = invoice_lib.new_aliases(draft, store.read_aliases())
    if fresh:
        store.add_aliases(fresh)

    if save_boxes:
        for line, found in learnable:
            store.update_product(line.product_code, {"units_per_box": found})
            store.append_audit([audit_lib.entry(
                actor, "PRODUCT_EDIT", feature="units_per_box",
                product_code=line.product_code,
                before=snap.per_box(line.product_code), after=found,
                note="명세표에서 읽음")])

    store.append_invoice_log({
        "vendor_code": draft.vendor_code, "vendor_name": vendor_name,
        "invoice_date": draft.invoice_date, "invoice_no": draft.invoice_no,
        "invoice_no_generated": "TRUE" if draft.invoice_no_generated else "FALSE",
        "line_count": len(ready), "total_qty": total_qty,
        "note_kind": "반품" if draft.is_return else ("혼합" if draft.mixed else "입고"),
        "total_amount": draft.total_amount or "",
        "image_ref": "", "created_by": actor.login_id,
        "note": f"미확정 {draft.pending_count}줄" if draft.pending_count else "",
    })

    for key in ("invoice_draft", "invoice_shots", "invoice_editor", "return_confirm"):
        st.session_state.pop(key, None)

    if draft.mixed:
        message = f"{result['written']}품목 처리했습니다. 입고 {in_qty:,}개, 반품 {out_qty:,}개."
    elif draft.is_return:
        message = f"{result['written']}품목 반품 처리했습니다. 재고가 {out_qty:,}개 줄었습니다."
    else:
        message = f"{result['written']}품목 입고했습니다."
    if fresh:
        message += f" 상품명 {len(fresh)}건을 기억해 다음부터 자동으로 붙습니다."
    if result["skipped"]:
        message += f" {result['skipped']}건은 이미 처리된 것이라 건너뛰었습니다."
    st.success(message)
    st.rerun()


# =====================================================================
# 판매
# =====================================================================
def page_sales(snap: Snapshot, data: dict, actor: User) -> None:
    if actor.can("sales.import"):
        _sales_import(snap, data, actor)
    else:
        st.caption("판매 파일 반영은 관리자만 할 수 있습니다.")

    if actor.can("stock.adjust"):
        _manual_entry(snap, actor)

    st.markdown("## 최근 판매")
    sales = [r for r in data["stock_ledger"] if r.get("reason") in ("COUPANG", "NAVER")]
    sales.sort(key=lambda r: str(r.get("occurred_on", "")), reverse=True)
    if sales:
        st.dataframe(
            pd.DataFrame([{
                "날짜": str(r.get("occurred_on", ""))[5:],
                "채널": r.get("party", ""),
                "상품": snap.all_products.get(r["product_code"], {}).get("product_name", r["product_code"]),
                "수량": -int(r.get("qty_change", 0)),
                "주문번호": r.get("ref_no", ""),
            } for r in sales[:60]]),
            hide_index=True, use_container_width=True, height=280,
        )
    else:
        st.info("판매 기록이 없습니다.")


def _sales_import(snap: Snapshot, data: dict, actor: User) -> None:
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

        def pick(label: str, key: str, container):
            index = columns.index(guessed[key]) + 1 if guessed[key] else 0
            value = container.selectbox(label, ["(없음)"] + columns, index=index)
            return None if value == "(없음)" else value

        col_a, col_b = st.columns(2)
        picked = {
            "order": pick("주문번호 열", "order", col_a),
            "qty": pick("수량 열", "qty", col_b),
            "name": pick("상품명 열", "name", col_a),
            "option": pick("옵션명 열", "option", col_b),
            "status": pick("주문상태 열", "status", col_a),
            "date": pick("주문일자 열", "date", col_b),
        }

        if st.button("확인하기"):
            if not picked["qty"] or not (picked["name"] or picked["option"]):
                st.error("상품명과 수량 열을 골라주세요.")
            else:
                matcher = parsers.build_matcher(data["product"], data["channel_mapping"])
                existing = get_store().existing_txn_keys()
                st.session_state.sale_preview = parsers.parse_sales(
                    frame, channel, picked, matcher, existing, today_kst())
                st.session_state.sale_channel = channel

    preview = st.session_state.get("sale_preview")
    if preview is None or preview.empty:
        return

    channel = st.session_state.get("sale_channel", channel)
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
        summary = audit_lib.entry(
            actor, "SALE_IMPORT",
            feature="쿠팡 판매반영" if channel == "COUPANG" else "네이버 판매반영",
            qty_change=-int(applicable["qty"].sum()),
            note=f"{len(applicable)}건 반영, 중복 {counts.get('중복', 0)}건, 실패 {len(failed)}건",
        )
        result = write_ledger(entries, actor, [summary])
        if not failed.empty:
            get_store().log_unmatched([{
                "channel": channel, "order_no": row["order_no"],
                "raw_name": row["raw_name"], "qty": int(row["qty"]),
                "occurred_on": row["occurred_on"], "resolved_code": "",
            } for _, row in failed.iterrows()])
        st.session_state.sale_preview = None
        st.success(f"{result['written']}건 차감했습니다.")
        st.rerun()


def _manual_entry(snap: Snapshot, actor: User) -> None:
    st.markdown("## 손으로 한 건 넣기")
    options = product_options(snap)
    if not options:
        return

    with st.form("manual_entry"):
        picked_name = st.selectbox("상품", list(options))
        reason = st.selectbox(
            "사유",
            ["COUPANG", "NAVER", "RETURN", "CANCEL", "DISPOSE", "DAMAGE", "SAMPLE", "ADJUST"],
            format_func=lambda r: REASONS[r],
        )
        qty = st.number_input("수량", min_value=0, value=1, step=1)
        memo = st.text_input("사유 메모", placeholder="실사 차이, 유통기한 폐기 등")
        st.caption(
            "재고 조정을 고르면 여기 넣은 수량이 '세어본 실제 수량'이 됩니다. "
            "장부 숫자를 덮어쓰지 않고, 차이만큼만 조정 기록이 남습니다."
        )

        if not st.form_submit_button("장부에 기록"):
            return

    code = options[picked_name]
    before = snap.stock(code)
    stamp = now_kst().strftime("%Y%m%d%H%M%S")

    if reason == "ADJUST":
        diff = int(qty) - before
        if diff == 0:
            st.info("장부와 같습니다. 기록하지 않았습니다.")
            return
        if not memo.strip():
            st.error("조정 사유를 적어주세요. 나중에 왜 고쳤는지 알 수 없게 됩니다.")
            return
        write_ledger([{
            "txn_key": manual_key("ADJUST", code, stamp), "product_code": code,
            "qty_change": diff, "reason": "ADJUST", "party": "실사", "ref_no": memo.strip(),
        }], actor, [audit_lib.entry(
            actor, "STOCK_ADJUST", product_code=code, before=before, after=int(qty),
            qty_change=diff, note=memo.strip(),
        )])
        st.success(f"{diff:+,}개 조정했습니다.")
        st.rerun()

    elif qty > 0:
        sign = -1 if reason in ("COUPANG", "NAVER", "DISPOSE", "DAMAGE", "SAMPLE") else 1
        change = sign * int(qty)
        action = {"DISPOSE": "DISPOSE", "DAMAGE": "DAMAGE", "RETURN": "RETURN",
                  "SAMPLE": "SAMPLE", "CANCEL": "CANCEL"}.get(reason, "SALE_MANUAL")
        write_ledger([{
            "txn_key": manual_key(reason, code, stamp), "product_code": code,
            "qty_change": change, "reason": reason,
            "party": {"COUPANG": "쿠팡", "NAVER": "네이버"}.get(reason, ""),
            "ref_no": memo.strip(),
        }], actor, [audit_lib.entry(
            actor, action, feature=REASONS[reason], product_code=code,
            before=before, after=before + change, qty_change=change, note=memo.strip(),
        )])
        st.success("장부에 기록했습니다.")
        st.rerun()


# =====================================================================
# 발주
# =====================================================================
def page_order(snap: Snapshot, data: dict, actor: User) -> None:
    orders = snap.order_list()
    if not orders:
        st.success("발주할 것이 없습니다. 모든 품목이 리드타임 안에서 여유가 있습니다.")
        return

    by_vendor: dict[str, list[str]] = {}
    for p in orders:
        by_vendor.setdefault(p.get("vendor_code", ""), []).append(p["product_code"])

    for vendor_code, codes in by_vendor.items():
        vendor = snap.vendors.get(vendor_code, {"vendor_name": vendor_code or "미지정"})
        amount = sum(snap.suggest_qty(c) * float(snap.all_products[c].get("purchase_price") or 0)
                     for c in codes)
        st.markdown(f"## {vendor.get('vendor_name', vendor_code)}")
        caption = f"리드타임 {vendor.get('lead_days', 3)}일"
        if actor.can("price.view"):
            caption += f" · 예상 {won(amount)}"
        st.caption(caption)

        for code in codes:
            item_row(snap, code, f"→ {snap.suggest_qty(code):,}",
                     f"{fmt_rate(snap.daily_rate(code))} · {fmt_left(snap.days_left(code))}")

        minimum = float(vendor.get("min_amount") or 0)
        if minimum and amount < minimum and actor.can("price.view"):
            st.warning(f"최소 발주금액 {won(minimum)}에 {won(minimum - amount)} 모자랍니다.")

        def pad(text: str, width: int) -> str:
            used = sum(2 if ord(ch) > 0x2000 else 1 for ch in text)
            return text + " " * max(1, width - used)

        lines = [
            pad(f"{snap.all_products[c]['product_name']} {snap.all_products[c].get('spec', '')}", 34)
            + f"{snap.suggest_qty(c):,} {snap.all_products[c].get('unit_label', '개')}"
            for c in codes
        ]
        body = ["태오상사 발주서", f"{today_kst()}",
                f"{vendor.get('vendor_name', vendor_code)} 귀중", "", *lines]
        if actor.can("price.view"):
            body += ["", f"합계 {won(amount)}"]

        with st.expander("발주서 만들기"):
            st.code("\n".join(body), language=None)
            st.caption(
                "오른쪽 위 복사 단추를 눌러 카카오톡이나 문자에 붙여넣으세요. "
                "발주서를 만들어도 재고는 바뀌지 않습니다. 물건이 들어온 날 입고로 기록하세요."
            )


# =====================================================================
# 사용자 관리
# =====================================================================
def page_users(snap: Snapshot, data: dict, actor: User) -> None:
    store = get_store()
    rows = store.read_users()

    st.markdown("## 등록된 사용자")
    st.dataframe(
        pd.DataFrame([{
            "아이디": r.get("login_id", ""),
            "이름": r.get("user_name", ""),
            "권한": ROLE_LABEL.get(str(r.get("role", "")).upper(), r.get("role", "")),
            "상태": "활성" if str(r.get("is_active", "TRUE")).upper() != "FALSE" else "비활성",
            "최근 로그인": str(r.get("last_login_at", "")).replace("T", " ")[:16],
        } for r in rows]),
        hide_index=True, use_container_width=True,
    )

    with st.expander("사용자 추가"):
        with st.form("new_user"):
            login_id = st.text_input("아이디", placeholder="영문 소문자, 숫자, 밑줄")
            user_name = st.text_input("이름")
            role = st.selectbox("권한", ["STAFF", "ADMIN"], format_func=lambda r: ROLE_LABEL[r])
            password = st.text_input("초기 비밀번호", type="password")
            st.caption("8자 이상. 만든 뒤 본인에게 알려주고 바꾸게 하세요.")

            if st.form_submit_button("사용자 만들기"):
                problem = (passwords.check_login_id(login_id.strip().lower())
                           or passwords.check_strength(password))
                if problem:
                    st.error(problem)
                elif not user_name.strip():
                    st.error("이름을 넣어주세요.")
                elif any(str(r.get("login_id", "")).lower() == login_id.strip().lower() for r in rows):
                    st.error("이미 있는 아이디입니다.")
                else:
                    store.add_user(build_user_row(next_user_id(rows), login_id,
                                                  user_name, role, password))
                    store.append_audit([audit_lib.entry(
                        actor, "USER_CREATE", after=f"{login_id.strip().lower()} ({role})",
                        note=user_name.strip(),
                    )])
                    st.success(f"{user_name} 계정을 만들었습니다.")
                    st.rerun()

    st.markdown("## 사용자 수정")
    editable = {f"{r.get('user_name', '')} ({r.get('login_id', '')})": r for r in rows}
    if not editable:
        return
    picked = st.selectbox("대상", list(editable))
    target = row_to_user(editable[picked])

    col_a, col_b = st.columns(2)
    with col_a:
        with st.form(f"edit_user_{target.user_id}"):
            new_name = st.text_input("이름", value=target.user_name)
            new_role = st.selectbox("권한", ["STAFF", "ADMIN"],
                                    index=0 if target.role == "STAFF" else 1,
                                    format_func=lambda r: ROLE_LABEL[r])
            active = st.checkbox("계정 활성", value=target.is_active)
            if st.form_submit_button("저장"):
                if target.user_id == actor.user_id and (new_role != "ADMIN" or not active):
                    st.error("자기 계정의 관리자 권한을 스스로 내리거나 잠글 수는 없습니다.")
                else:
                    changes, audits = {}, []
                    if new_name.strip() != target.user_name:
                        changes["user_name"] = new_name.strip()
                        audits.append(audit_lib.entry(actor, "USER_EDIT", feature="이름",
                                                      before=target.user_name,
                                                      after=new_name.strip(), note=target.login_id))
                    if new_role != target.role:
                        changes["role"] = new_role
                        audits.append(audit_lib.entry(actor, "USER_EDIT", feature="권한",
                                                      before=target.role, after=new_role,
                                                      note=target.login_id))
                    if active != target.is_active:
                        changes["is_active"] = "TRUE" if active else "FALSE"
                        audits.append(audit_lib.entry(
                            actor, "USER_ENABLE" if active else "USER_DISABLE",
                            before="활성" if target.is_active else "비활성",
                            after="활성" if active else "비활성", note=target.login_id))
                    if not changes:
                        st.info("바뀐 것이 없습니다.")
                    else:
                        store.update_user(target.user_id, changes)
                        store.append_audit(audits)
                        st.success("저장했습니다.")
                        st.rerun()

    with col_b:
        st.write("**비밀번호 초기화**")
        st.caption("임시 비밀번호를 만들어 본인에게 전달하세요.")
        if st.button("초기화", key=f"reset_{target.user_id}"):
            temporary = passwords.generate_password()
            store.update_user(target.user_id, {
                "password_hash": passwords.hash_password(temporary),
                "password_changed_at": now_kst().isoformat(timespec="seconds"),
                "must_change_password": "TRUE",
            })
            store.append_audit([audit_lib.entry(actor, "USER_RESET_PW", note=target.login_id)])
            st.session_state[f"temp_{target.user_id}"] = temporary
            st.rerun()

        temporary = st.session_state.pop(f"temp_{target.user_id}", None)
        if temporary:
            st.code(temporary, language=None)
            st.warning("이 화면을 벗어나면 다시 볼 수 없습니다. 지금 전달하세요.")

    st.caption(
        "계정은 지우지 않고 비활성으로 둡니다. 지우면 그 사람이 남긴 과거 작업기록에서 "
        "누구인지 알 수 없게 됩니다."
    )

    st.markdown("## 최근 로그인 기록")
    log = store.read_login_log(120)
    if log:
        st.dataframe(
            pd.DataFrame([{
                "일시": str(r.get("occurred_at", "")).replace("T", " ")[:16],
                "아이디": r.get("login_id", ""),
                "결과": "성공" if r.get("result") == "SUCCESS" else "실패",
                "이름": r.get("user_name", ""),
                "비고": r.get("note", ""),
            } for r in reversed(log)]),
            hide_index=True, use_container_width=True, height=260,
        )


# =====================================================================
# 작업 기록
# =====================================================================
def page_audit(snap: Snapshot, data: dict, actor: User) -> None:
    store = get_store()
    names = {c: p.get("product_name", c) for c, p in snap.all_products.items()}
    merged = audit_lib.merge_view(store.read_audit(), data["stock_ledger"], names, REASONS)

    col_a, col_b = st.columns(2)
    start = col_a.date_input("시작일", value=today_kst().replace(day=1))
    end = col_b.date_input("종료일", value=today_kst())

    users = sorted({r["_user"] for r in merged if r["_user"]})
    actions = sorted({r["작업"] for r in merged if r["작업"]})
    col_c, col_d = st.columns(2)
    who = col_c.selectbox("사용자", ["전체"] + users)
    what = col_d.selectbox("작업", ["전체"] + actions)

    keyword = st.text_input("상품명 또는 번호", placeholder="비워두면 전체")
    source = st.radio("보기", ["전체", "재고원장", "작업기록"], horizontal=True)

    rows = []
    for row in merged:
        stamp = row["_at"][:10]
        if stamp and not (start.isoformat() <= stamp <= end.isoformat()):
            continue
        if who != "전체" and row["_user"] != who:
            continue
        if what != "전체" and row["작업"] != what:
            continue
        if source != "전체" and row["_source"] != source:
            continue
        if keyword:
            hay = f"{row['대상']} {row['번호']} {row['비고']} {row['_code']}".lower()
            if keyword.lower() not in hay:
                continue
        rows.append({k: v for k, v in row.items() if not k.startswith("_")})

    st.caption(f"{len(rows):,}건")
    if rows:
        frame = pd.DataFrame(rows)
        st.dataframe(frame, hide_index=True, use_container_width=True, height=420)
        st.download_button(
            "조회 결과 내려받기 (CSV)",
            frame.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"태오상사-작업기록-{start}~{end}.csv", mime="text/csv",
        )
    else:
        st.info("조건에 맞는 기록이 없습니다.")

    st.caption(
        "재고를 움직인 일은 재고원장에, 값이 바뀐 일과 사용자 관리는 작업기록에 남습니다. "
        "두 기록 모두 앱에서 고치거나 지울 수 없습니다."
    )


# =====================================================================
# 실행
# =====================================================================
PAGES = [
    ("대시보드", "dashboard.view", page_dashboard),
    ("재고", "stock.view", page_stock),
    ("입고", "inbound.create", page_inbound),
    ("판매", "sales.view", page_sales),
    ("발주", "order.view", page_order),
    ("사용자", "user.manage", page_users),
    ("작업기록", "audit.view", page_audit),
]

current_user = require_login()   # 통과하지 못하면 아래는 실행되지 않는다

st.title("태오상사 재고")
snapshot, sheet_data = get_snapshot()
st.caption(
    f"{now_kst():%m월 %d일 %H:%M} 기준 · 상품 {len(snapshot.products)}개 · "
    f"장부 {len(sheet_data['stock_ledger']):,}줄"
)

# 계정 메뉴는 사이드바가 아니라 본문 위쪽에 둔다.
# 사이드바를 여는 화살표는 폰에서 너무 작아, 못 찾으면 로그아웃도 할 수 없다.
with st.expander(f"{current_user.user_name} · {current_user.role_label}"):
    st.caption(
        f"아이디 {current_user.login_id} · "
        f"로그인 유지 {str(st.session_state.get('expires_at', ''))[11:16]}까지"
    )

    if st.button("로그아웃"):
        for key in ("user", "expires_at", "basket", "sale_preview"):
            st.session_state.pop(key, None)
        st.rerun()

    st.markdown("**비밀번호 바꾸기**")
    with st.form("change_pw"):
        current_pw = st.text_input("현재 비밀번호", type="password")
        fresh = st.text_input("새 비밀번호", type="password")
        again = st.text_input("새 비밀번호 확인", type="password")
        st.caption("8자 이상. taeo나 admin처럼 짐작하기 쉬운 말은 넣을 수 없습니다.")

        if st.form_submit_button("바꾸기"):
            store = get_store()
            row = next((r for r in store.read_users()
                        if str(r.get("user_id")) == current_user.user_id), None)
            problem = passwords.check_strength(fresh)
            if row is None or not passwords.verify_password(
                    current_pw, str(row.get("password_hash", ""))):
                st.error("현재 비밀번호가 맞지 않습니다.")
            elif fresh != again:
                st.error("새 비밀번호가 서로 다릅니다.")
            elif problem:
                st.error(problem)
            else:
                store.update_user(current_user.user_id, {
                    "password_hash": passwords.hash_password(fresh),
                    "password_changed_at": now_kst().isoformat(timespec="seconds"),
                    "must_change_password": "FALSE",
                })
                store.append_audit([audit_lib.entry(current_user, "PASSWORD_CHANGE")])
                st.success("바꿨습니다. 다음 로그인부터 새 비밀번호를 쓰세요.")

visible = [page for page in PAGES if current_user.can(page[1])]
if not visible:
    st.error("사용할 수 있는 기능이 없습니다. 관리자에게 권한을 요청하세요.")
    st.stop()

for tab_widget, (label, permission, render) in zip(st.tabs([p[0] for p in visible]), visible):
    with tab_widget:
        render(snapshot, sheet_data, current_user)
