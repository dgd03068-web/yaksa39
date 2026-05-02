"""One-shot script: insert 2023 (제74회) 약사 국가시험 final answers into questions.db.

Run: python -m parsers._insert_2023_answers
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import DB_PATH  # noqa: E402

# Subject names per session (교시) — normalized
SUBJECTS = [
    ("생명약학", 1),
    ("산업약학", 2),
    ("임상·실무약학1", 3),
    ("임상·실무약학2", 4),
    ("보건·의약관계법규", 4),
]

# 1교시 생명약학 1-100
SESSION1 = [
    1, 4, 2, 5, 5, 5, 2, 1, 4, 2,
    5, 1, 3, 5, 4, 4, 3, 2, 3, 3,
    5, 3, 2, 4, 1, 4, 5, 2, 3, 4,
    4, 4, 1, 1, 5, 5, 3, 1, 5, 5,
    1, 1, 2, 5, 3, 5, 2, 2, 2, 3,
    1, 5, 5, 4, 1, 2, 2, 1, 1, 2,
    5, 4, 5, 1, 1, 5, 1, 4, 5, 5,
    3, 4, 3, 4, 1, 5, 1, 1, 3, 3,
    2, 4, 1, 4, 1, 5, 3, 4, 3, 1,
    1, 3, 3, 2, 4, 1, 4, 4, 4, 3,
]

# 2교시 산업약학 1-90
SESSION2 = [
    3, 2, 5, 4, 2, 5, 3, 5, 3, 3,
    4, 1, 5, 1, 5, 2, 4, 5, 5, 2,
    3, 1, 5, 3, 1, 4, 2, 5, 5, 4,
    4, 2, 3, 4, 1, 1, 2, 2, 4, 3,
    5, 4, 5, 4, 4, 2, 4, 5, 1, 1,
    5, 4, 4, 5, 3, 4, 3, 3, 5, 4,
    1, 4, 5, 2, 2, 1, 4, 3, 1, 2,
    3, 2, 5, 2, 4, 1, 4, 1, 3, 3,
    2, 1, 2, 3, 5, 1, 1, 3, 5, 4,
]

# 3교시 임상·실무약학1 1-77
SESSION3 = [
    1, 4, 5, 5, 1, 4, 5, 5, 2, 2,
    5, 2, 2, 4, 2, 2, 5, 2, 2, 4,
    4, 2, 3, 2, 4, 1, 2, 5, 3, 4,
    4, 3, 2, 5, 2, 3, 2, 5, 4, 5,
    4, 1, 4, 3, 5, 1, 4, 4, 5, 4,
    5, 5, 3, 4, 5, 4, 2, 2, 4, 1,
    1, 1, 1, 4, 2, 5, 1, 1, 2, 5,
    5, 2, 5, 4, 4, 1, 5,
]

# 4교시 임상·실무약학2 1-63
SESSION4_CLINICAL = [
    1, 3, 4, 5, 5, 2, 5, 2, 4, 4,
    5, 2, 1, 4, 2, 4, 4, 5, 2, 3,
    5, 4, 3, 1, 1, 4, 1, 3, 5, 5,
    2, 1, 3, 1, 3, 4, 4, 4, 5, 3,
    4, 2, 2, 3, 3, 4, 5, 3, 4, 2,
    3, 2, 5, 1, 5, 5, 5, 3, 4, 4,
    1, 5, 4,
]

# 4교시 보건·의약관계법규 64-83
SESSION4_LAW = [
    5, 4, 3, 4, 5, 5,
    3, 2, 3, 1, 2, 1, 5, 3, 4, 4,
    5, 3, 2, 1,
]


def main():
    rows = []
    for i, ans in enumerate(SESSION1, start=1):
        rows.append((1, "생명약학", i, ans))
    for i, ans in enumerate(SESSION2, start=1):
        rows.append((2, "산업약학", i, ans))
    for i, ans in enumerate(SESSION3, start=1):
        rows.append((3, "임상·실무약학1", i, ans))
    for i, ans in enumerate(SESSION4_CLINICAL, start=1):
        rows.append((4, "임상·실무약학2", i, ans))
    for i, ans in enumerate(SESSION4_LAW, start=64):
        rows.append((4, "보건·의약관계법규", i, ans))

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    cur.execute("INSERT OR IGNORE INTO exams (year, round) VALUES (2023, 74);")
    cur.execute("SELECT id FROM exams WHERE year=2023 AND round=74;")
    exam_id = cur.fetchone()[0]

    for name, session in SUBJECTS:
        cur.execute(
            "INSERT OR IGNORE INTO subjects (name, session) VALUES (?, ?);",
            (name, session),
        )

    subj_ids = {}
    for name, _ in SUBJECTS:
        cur.execute("SELECT id FROM subjects WHERE name=?;", (name,))
        subj_ids[name] = cur.fetchone()[0]

    inserted = 0
    for session, subj_name, qnum, ans in rows:
        sid = subj_ids[subj_name]
        cur.execute(
            "INSERT OR IGNORE INTO questions (exam_id, subject_id, question_number, body, answer) "
            "VALUES (?, ?, ?, '', ?);",
            (exam_id, sid, qnum, ans),
        )
        cur.execute(
            "UPDATE questions SET answer=? WHERE exam_id=? AND subject_id=? AND question_number=?;",
            (ans, exam_id, sid, qnum),
        )
        inserted += 1

    conn.commit()
    conn.close()
    print(f"2023년 {inserted}개 정답 입력")


if __name__ == "__main__":
    main()
