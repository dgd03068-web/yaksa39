"""SQLite schema and connection helper."""
import os
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config import DB_PATH


# ── Streamlit Cloud 감지: /mount/src/ 는 read-only → DB를 /tmp로 복사 후 사용 ──
# 매 호출마다 mtime 체크해 source DB가 갱신되면 자동 재복사. (git push 후 옛 cache 유지 방지)
def _resolve_db_path() -> Path:
    src = Path(DB_PATH)
    is_readonly_mount = str(src).startswith("/mount/") or os.environ.get("STREAMLIT_RUNTIME_ENV") == "cloud"
    if not is_readonly_mount:
        return src
    dst = Path("/tmp/yaksa39_questions.db")
    needs_copy = (
        not dst.exists()
        or dst.stat().st_mtime < src.stat().st_mtime
        or dst.stat().st_size != src.stat().st_size
    )
    if needs_copy:
        shutil.copy2(src, dst)
    return dst

SCHEMA = """
CREATE TABLE IF NOT EXISTS exams (
  id INTEGER PRIMARY KEY,
  year INTEGER NOT NULL,
  round INTEGER NOT NULL,
  exam_date TEXT,
  UNIQUE(year, round)
);

CREATE TABLE IF NOT EXISTS subjects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  session INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
  id INTEGER PRIMARY KEY,
  subject_id INTEGER REFERENCES subjects(id),
  chapter_no INTEGER,
  name TEXT,
  parent_id INTEGER REFERENCES chapters(id)
);

CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY,
  exam_id INTEGER REFERENCES exams(id),
  subject_id INTEGER REFERENCES subjects(id),
  chapter_id INTEGER REFERENCES chapters(id),
  question_number INTEGER NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  has_image INTEGER NOT NULL DEFAULT 0,
  answer INTEGER,
  explanation TEXT,
  explanation_source TEXT,
  concept_tag TEXT,
  is_skipped INTEGER NOT NULL DEFAULT 0,
  UNIQUE(exam_id, subject_id, question_number)
);

CREATE TABLE IF NOT EXISTS choices (
  id INTEGER PRIMARY KEY,
  question_id INTEGER REFERENCES questions(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  text TEXT NOT NULL,
  UNIQUE(question_id, number)
);

CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY,
  question_id INTEGER REFERENCES questions(id) ON DELETE CASCADE,
  attempted_at TEXT NOT NULL,
  is_correct INTEGER,
  selected_answer INTEGER,
  mode TEXT,
  elapsed_sec INTEGER
);

CREATE TABLE IF NOT EXISTS bookmarks (
  question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drugs (
  id INTEGER PRIMARY KEY,
  name_ko TEXT NOT NULL,
  name_en TEXT NOT NULL,
  cid INTEGER,                 -- PubChem CID
  smiles TEXT,                 -- canonical SMILES
  mw REAL,                     -- 분자량
  drug_class TEXT,             -- 약효군 (예: "Statin", "ACE inhibitor")
  description TEXT,            -- 핵심 설명 1~2줄
  image_url TEXT,              -- PubChem 구조식 이미지 URL
  exam_count INTEGER DEFAULT 0,-- 5년 출제 빈도
  UNIQUE(name_en)
);
CREATE INDEX IF NOT EXISTS idx_drugs_name_ko ON drugs(name_ko);
CREATE INDEX IF NOT EXISTS idx_drugs_class ON drugs(drug_class);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY,
  question_id INTEGER REFERENCES questions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,      -- 'answer_wrong' / 'explanation' / 'typo' / 'other'
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS review_schedule (
  question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
  next_review_at TEXT NOT NULL,
  interval_days REAL NOT NULL DEFAULT 1.0,
  ease_factor REAL NOT NULL DEFAULT 2.5,
  last_attempted_at TEXT
);

CREATE TABLE IF NOT EXISTS worksheets (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  filter_json TEXT,
  pdf_path TEXT
);

CREATE TABLE IF NOT EXISTS lecture_files (
  id INTEGER PRIMARY KEY,
  original_path TEXT,
  organized_path TEXT,
  subject_id INTEGER REFERENCES subjects(id),
  chapter_id INTEGER REFERENCES chapters(id),
  confidence REAL,
  classified_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_questions_exam_subject ON questions(exam_id, subject_id);
CREATE INDEX IF NOT EXISTS idx_questions_chapter ON questions(chapter_id);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
CREATE INDEX IF NOT EXISTS idx_choices_question ON choices(question_id);
"""


def init_db(path: Path = DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn(path: Path = None):
    # 매 호출마다 _resolve_db_path() — source DB가 갱신되면 즉시 반영
    target = path or _resolve_db_path()
    conn = sqlite3.connect(target, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def reset_db(path: Path = DB_PATH) -> None:
    """Delete and recreate the DB. Useful when re-running parsers."""
    if path.exists():
        path.unlink()
    init_db(path)


if __name__ == "__main__":
    init_db()
    print(f"Initialized DB at {DB_PATH}")
