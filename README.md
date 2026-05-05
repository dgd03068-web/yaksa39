# 2026 약사국가고시 학습지

5개년(2021-2025) 기출문제 1,798개 + 약물치료학·약제학 해설 511개를 단원별로 골라 인쇄용 PDF로 출력하는 학습 도구.

## 사용법 (배포된 친구용)

1. URL 접속 → 비밀번호 입력
2. 사이드바에서 단원 선택 (약물치료학 56단원 / 약제학 8단원 등)
3. **학습지 출력** → PDF 다운로드 → 인쇄해서 풀이

## 로컬 실행

### 의존성 (macOS)

```sh
brew install pango cairo gdk-pixbuf libffi
python3 -m venv ~/.venvs/yaksa39
~/.venvs/yaksa39/bin/pip install -r requirements.txt
```

### 실행

```sh
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
  ~/.venvs/yaksa39/bin/streamlit run app.py
```

## 배포 (Streamlit Cloud)

1. GitHub repo로 push
2. https://share.streamlit.io 에서 repo 연결
3. **Settings → Secrets** 에 비밀번호 등록:
   ```toml
   app_password = "your-password-here"
   ```
4. Main file path: `app.py`

`packages.txt`(시스템 라이브러리) + `requirements.txt`(파이썬 패키지)는 자동 설치됨.

## 구조

```
app/
├── app.py                 ← Streamlit 진입점
├── config.py              ← 경로 설정
├── db.py                  ← SQLite 헬퍼
├── data/questions.db      ← 문제·해설 DB
├── fonts/Pretendard-*.woff2 ← 한국어 폰트 5 weight
├── generator/
│   ├── make_pdf.py        ← WeasyPrint PDF 생성
│   └── templates/
│       ├── base.css       ← 스타일 (폰트·여백·2단)
│       ├── worksheet.html ← 문제 영역
│       └── solutions.html ← 정답·해설
├── parsers/               ← PDF → DB (이미 처리 완료)
└── ai/                    ← AI 해설 생성·검수 (개발 중)
```
