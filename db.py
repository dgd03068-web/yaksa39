"""SQLite schema and connection helper."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config import DB_PATH

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
  is_correct INTEGER
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
CREATE INDEX IF NOT EXISTS idx_choices_question ON choices(question_id);
"""


def init_db(path: Path = DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn(path: Path = DB_PATH):
    conn = sqlite3.connect(path)
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
