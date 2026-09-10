# 태오상사 재고 장부

구글 스프레드시트를 데이터베이스로 쓰고, Streamlit Cloud에서 무료로 돌아가는 재고관리 앱.
폰에서 주소를 열어 홈 화면에 추가하면 앱처럼 쓸 수 있다.

## 이 시스템의 원칙

**현재재고를 어디에도 저장하지 않는다.** 원장(`stock_ledger` 탭)에 줄을 더해서 만든다.
스프레드시트에 `현재재고` 열이 없는 것은 실수가 아니라 설계다.

재고가 틀렸을 때 "왜 틀렸는지"를 항상 되짚을 수 있고, 실수로 숫자를 덮어써서
복구 불가능해지는 일이 생기지 않는다. 재고를 바꾸는 방법은 원장에 줄을 하나 더하는 것뿐이다.

---

## 준비 순서

전체 30분 정도. 순서를 건너뛰면 나중에 원인을 찾기 어려우니 차례대로 한다.

### 1. 스프레드시트 만들기

1. [sheets.new](https://sheets.new) 로 새 스프레드시트를 만든다.
2. 이름을 `태오상사 재고DB` 로 바꾼다.
3. 주소창에서 **ID**를 복사해 둔다. `/d/` 와 `/edit` 사이의 긴 문자열이다.

```
https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890/edit
                                       └──────────── 이 부분이 ID ────────────┘
```

### 2. 서비스 계정 만들기

앱이 스프레드시트를 읽고 쓰려면 "로봇 계정"이 필요하다. 사장님 개인 구글 계정을
쓰지 않는 이유는, 앱에 계정 전체 권한을 주지 않고 이 스프레드시트 하나만
건드리게 하기 위해서다.

1. [console.cloud.google.com](https://console.cloud.google.com) 접속
2. 위쪽 프로젝트 선택 → **새 프로젝트** → 이름 `taeo-stock` → 만들기
3. 검색창에 `Google Sheets API` → **사용** 누르기
4. 검색창에 `Google Drive API` → **사용** 누르기
5. 왼쪽 메뉴 **API 및 서비스 → 사용자 인증 정보**
6. **사용자 인증 정보 만들기 → 서비스 계정**
   - 이름: `taeo-stock`
   - 역할은 지정하지 않고 넘어가도 된다
7. 만들어진 서비스 계정을 눌러 **키 → 키 추가 → 새 키 만들기 → JSON**
8. 파일이 내려받아진다. **이 파일이 열쇠다. 누구에게도 보내지 말 것.**

### 3. 스프레드시트에 권한 주기

내려받은 JSON 파일을 열면 `client_email` 이 있다.
`taeo-stock@taeo-stock-123456.iam.gserviceaccount.com` 같은 주소다.

1. 스프레드시트 오른쪽 위 **공유** 누르기
2. 그 주소를 붙여넣고 **편집자** 권한으로 추가
3. "알림 보내기" 체크는 해제

이걸 빼먹으면 앱이 `SpreadsheetNotFound` 오류를 낸다. 가장 흔한 실수다.

### 4. 탭 만들기

내 컴퓨터에서 한 번만 실행한다.

```bash
git clone https://github.com/<내계정>/taeo-stock.git
cd taeo-stock
pip install -r requirements.txt

# 내려받은 JSON을 service_account.json 이라는 이름으로 이 폴더에 둔다
python scripts/bootstrap_sheet.py --key <스프레드시트ID> --creds service_account.json
```

먼저 예시 데이터로 화면을 보고 싶으면 `--demo` 를 붙인다.
상품 15개와 60일치 판매 기록이 들어간다.

```bash
python scripts/bootstrap_sheet.py --key <ID> --creds service_account.json --demo
```

실제로 장사에 쓸 때는 붙이지 않는다.

### 5. GitHub에 올리기

```bash
git init
git add .
git commit -m "태오상사 재고 장부"
git branch -M main
git remote add origin https://github.com/<내계정>/taeo-stock.git
git push -u origin main
```

`.gitignore` 에 `service_account.json` 과 `secrets.toml` 이 들어 있으니
열쇠 파일은 올라가지 않는다. `git status` 로 한 번 확인하고 올린다.

저장소는 **공개(public)로 만들어도 된다.** 소스코드에는 열쇠가 없다.
Streamlit Cloud 무료 요금제는 공개 저장소에서 더 넉넉하다.

### 6. Streamlit Cloud에 올리기

1. [share.streamlit.io](https://share.streamlit.io) 에서 GitHub 계정으로 로그인
2. **Create app** → 저장소 선택
   - Branch: `main`
   - Main file path: `streamlit_app.py`
   - **App URL**: 여기서 정한 이름이 그대로 주소가 된다.
     `taeo-stock` 이라고 쓰면 `https://taeo-stock.streamlit.app` 이다. 무료다.
3. **Advanced settings → Secrets** 에 아래를 붙여넣는다.
   JSON 파일을 열어 값을 옮겨 적는다.

```toml
sheet_key = "스프레드시트ID"

[gcp_service_account]
type = "service_account"
project_id = "taeo-stock-123456"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n"
client_email = "taeo-stock@taeo-stock-123456.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

`private_key` 는 반드시 큰따옴표로 감싸고 `\n` 을 그대로 둔다.
JSON 파일에 있는 그대로 복사하면 된다. 줄바꿈으로 풀어 쓰면 안 된다.

4. **Deploy** 누르고 2~3분 기다린다.

### 7. 폰에 앱처럼 설치

받은 주소를 폰에서 연다.

- **아이폰(사파리)**: 아래 공유 단추 → 홈 화면에 추가
- **안드로이드(크롬)**: 오른쪽 위 점 세 개 → 홈 화면에 추가

아이콘이 생기고 주소창 없이 전체 화면으로 뜬다.

---

## 화면

| 탭 | 하는 일 |
|---|---|
| 대시보드 | 발주할 품목 수, 재고 금액, 오늘 판매, 장부가 어긋난 신호 |
| 재고 | 검색, 상품별 이력과 판매 속도, 판매처 상품명 연결, 상품 추가 |
| 입고 | 거래처와 명세표 번호로 입고. 같은 번호는 두 번 반영되지 않는다 |
| 판매 | 쿠팡·네이버 파일 올리기. 열 이름을 알아서 찾고 사람이 확인 후 반영 |
| 발주 | 매입처별 권장 수량과 카톡에 붙여넣을 발주서 |

---

## 자주 막히는 곳

**`SpreadsheetNotFound`**
서비스 계정 이메일에게 스프레드시트 편집 권한을 주지 않았다. 3번 단계로 돌아간다.

**`'product' 탭이 없습니다`**
4번 단계의 `bootstrap_sheet.py` 를 실행하지 않았다.

**`APIError: 429` 또는 화면이 자주 멎는다**
구글 API 호출 한도(분당 60회)에 걸렸다. 앱은 3분간 결과를 기억했다가 다시 쓰므로
평소에는 걸리지 않는다. 새로고침을 연달아 누르지 않는다.

**화면이 느려졌다**
원장이 2만 줄을 넘었을 것이다. 해마다 한 번, 지난해 기록을 `stock_ledger_2026`
같은 새 탭으로 옮기고, 그 시점 재고를 `OPENING` 한 줄로 넣어 시작을 새로 잡는다.
과거 기록은 새 탭에 그대로 남는다.

**엑셀 열 이름이 안 맞는다**
쿠팡과 네이버는 열 이름을 종종 바꾼다. 판매 탭에서 열을 직접 고를 수 있게 해뒀다.
자주 쓰는 이름이면 `lib/parsers.py` 의 `COLUMN_HINTS` 에 추가하면 다음부터 자동으로 잡힌다.

---

## 직접 돌려보기

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# secrets.toml 을 채운 뒤
streamlit run streamlit_app.py
```

계산 로직 검증은 구글 계정 없이도 된다.

```bash
python tests/test_core.py
```

---

## 알아둘 것

**스프레드시트는 진짜 데이터베이스가 아니다.** UNIQUE 제약이 없어서 중복 방지를
`lib/sheets.py` 의 `append_ledger` 가 코드로 막고 있다. 혼자 쓰는 동안은 문제없지만,
두 사람이 같은 순간에 같은 파일을 올리면 둘 다 통과할 수 있다.

직원이 늘어 여러 명이 동시에 입력하게 되거나, 원장이 5만 줄을 넘으면
Postgres(Supabase)로 옮기는 게 맞다. 그때는 `lib/sheets.py` 만 갈아끼우면 된다.
`lib/inventory.py` 의 계산 로직과 화면은 그대로 쓴다. 그러려고 나눠 놓았다.

**스프레드시트에서 숫자를 직접 고치지 않는다.** 특히 원장 탭의 기존 줄을 고치면
어디서 틀어졌는지 알 수 없게 된다. 고칠 일이 생기면 판매 탭의 재고 조정을 쓴다.
차이만큼만 새 줄로 기록되고 이유가 남는다.

**월 1회 부분 실사를 권한다.** 파손·분실·오배송으로 오차는 반드시 누적된다.
잘 나가는 상품 위주로 세어보고 재고 조정으로 맞추면, 오차가 커지기 전에 잡힌다.
