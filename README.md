# KIS Real-time K-Stock (Streamlit)

한국투자증권 Open API(`python-kis`)를 이용해 한국 주식 시세/차트를 조회하는 Streamlit 앱입니다.

## 1) 로컬 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 2) Secrets 설정 (필수)

민감정보는 절대 코드/레포에 저장하지 말고 `st.secrets`로만 관리합니다.

```toml
# .streamlit/secrets.toml (로컬 전용)
[kis]
account = "12345678-01"
id = "KIS_LOGIN_ID"
appkey = "KIS_APP_KEY"
secretkey = "KIS_SECRET_KEY"
token = '{"access_token":"...","access_token_token_expired":"2026-02-07 23:59:59","token_type":"Bearer","expires_in":86400}'
```

- `token`은 선택값입니다. 넣으면 기존 `token.json`을 주입하고, 없으면 `keep_token=True`로 자동 발급/저장합니다.

## 3) Streamlit Cloud 배포

1. GitHub에 앱 코드만 push
2. Streamlit Cloud 앱 생성
3. `Settings -> Secrets`에 위 TOML 내용 등록
4. 앱 재시작

## 보안 원칙

- `.streamlit/secrets.toml`, `secret.json`, `token.json`은 Git에 커밋 금지
- 로그인 ID, 계좌번호, App Key/Secret Key, Access Token은 로그 출력 금지
- 인증 정보는 코드 내 하드코딩 금지
