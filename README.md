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
id = "KIS_LOGIN_ID"
appkey = "KIS_APP_KEY"
secretkey = "KIS_SECRET_KEY"
account = "12345678-01"
virtual_id = "KIS_VIRTUAL_ID"
virtual_appkey = "KIS_VIRTUAL_APP_KEY"
virtual_secretkey = "KIS_VIRTUAL_SECRET_KEY"
virtual_account = "87654321-01"
```

## 3) Streamlit Cloud 배포

1. GitHub에 앱 코드만 push
2. Streamlit Cloud 앱 생성
3. `Settings -> Secrets`에 위 TOML 내용 등록
4. 앱 재시작

## 보안 원칙

- `.streamlit/secrets.toml`, `secret.json`, `token.json`은 Git에 커밋 금지
- 로그인 ID, 계좌번호, App Key/Secret Key, Access Token은 로그 출력 금지
- 인증 정보는 코드 내 하드코딩 금지
