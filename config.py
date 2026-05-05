"""Central paths for the app. Edit if folders move."""
from pathlib import Path

# config.py 자체가 app/ 안에 있음.
# 로컬: app/ 의 부모 = "국시 학습 프로그램/"
# 클라우드(repo 루트=app/): app/ 의 부모 = /mount/src/yaksa39 의 부모 (외부 자료 없음)
APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent

# 입력 자료
EXAM_FOLDERS = {
    2021: ROOT / "21 국시",
    2022: ROOT / "22 국시",
    2023: ROOT / "23 국시",
    2024: ROOT / "24 국시",
    2025: ROOT / "25 국시",
}
GOODNOTE_FOLDER = ROOT / "Good note"
LECTURE_INBOX = ROOT / "강의파일" / "inbox"
LECTURE_REVIEW = ROOT / "강의파일" / "inbox" / "_review"
LECTURE_ORGANIZED = ROOT / "강의파일"

# 출력
DB_PATH = APP_DIR / "data" / "questions.db"
TEMPLATE_DIR = APP_DIR / "generator" / "templates"

# 클라우드(/mount/src/...)는 read-only → /tmp로. 로컬은 app/output/
import os as _os
import tempfile as _tempfile
if str(APP_DIR).startswith("/mount/") or _os.environ.get("STREAMLIT_RUNTIME_ENV") == "cloud":
    OUTPUT_DIR = Path(_tempfile.gettempdir()) / "yaksa39_output"
else:
    OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 회차 매핑 (연도 → 회차)
ROUND_MAP = {2021: 72, 2022: 73, 2023: 74, 2024: 75, 2025: 76}

# 시험일 매핑 (확인된 것만 — 답안 PDF에서 추출하면 더 정확)
EXAM_DATE = {
    2024: "2024-01-19",
    2025: "2025-01-24",
}

# 과목 → 교시 매핑 (76회 답안 PDF 기준)
SUBJECT_SESSION = {
    "생명약학": 1,
    "산업약학": 2,
    "임상·실무약학1": 3,
    "임상·실무약학2": 4,
    "보건·의약관계법규": 4,
}
