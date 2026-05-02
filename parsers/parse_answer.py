"""Parse the official answer-key PDF (image-based) via macOS Vision OCR."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import fitz

# Make sibling modules importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import EXAM_FOLDERS, ROUND_MAP, EXAM_DATE  # noqa: E402
from db import get_conn, init_db  # noqa: E402
from parsers._ocr import nfc, ocr_pdf_page, group_into_rows  # noqa: E402

# 답안 PDF 파일명 패턴
ANSWER_FILE_PATTERNS = [
    re.compile(r"1\s*~\s*4교시.*최종답안.*\.pdf$"),
    re.compile(r"최종답안.*\.pdf$"),
]

SESSION_RE = re.compile(r"^\s*(\d+)\s*교시\s*$")
NUMBER_ONLY = re.compile(r"^\s*(\d+)\s*$")

# 과목명 정규화 매핑: OCR이 가운뎃점을 다양하게 인식 (·/•/. + 공백)
# canonical → 표시 이름
SUBJECT_CANONICAL = {
    "생명약학": "생명약학",
    "산업약학": "산업약학",
    "임상실무약학1": "임상·실무약학1",
    "임상실무약학2": "임상·실무약학2",
    "보건의약관계법규": "보건·의약관계법규",
}


def canonicalize_subject(raw: str) -> str | None:
    """OCR로 들어온 과목명을 canonical 표시명으로 변환. 못 찾으면 None."""
    if not raw:
        return None
    # 모든 분리자(·, •, ., 공백) 제거
    key = re.sub(r"[·•·.\s]+", "", raw)
    return SUBJECT_CANONICAL.get(key)

# 답안 PDF 표 컬럼 X 중심 (정규화 0..1) — 첫 페이지 헤더에서 측정
COL_X_CENTERS = {
    "session": 0.20,
    "subject": 0.44,
    "qnum": 0.68,
    "answer": 0.80,
}
COL_TOLERANCE = 0.05  # qnum-answer gap = 0.12, 절반 이하


def find_answer_pdf(folder: Path) -> Path:
    for f in folder.iterdir():
        name = nfc(f.name)
        for pat in ANSWER_FILE_PATTERNS:
            if pat.search(name):
                return f
    raise FileNotFoundError(f"답안 PDF를 찾을 수 없음: {folder}")


def assign_column(x_center: float) -> str | None:
    """Pick the closest column by x_center."""
    best, best_dist = None, 999.0
    for name, cx in COL_X_CENTERS.items():
        d = abs(x_center - cx)
        if d < best_dist:
            best_dist = d
            best = name
    return best if best_dist < COL_TOLERANCE else None


def parse_answer_pdf(pdf_path: Path) -> list[dict]:
    """OCR every page, group into rows, extract (session, subject, qnum, answer)."""
    rows_out: list[dict] = []
    doc = fitz.open(pdf_path)
    n_pages = len(doc)
    doc.close()
    for page_idx in range(n_pages):
        boxes = ocr_pdf_page(pdf_path, page_idx, dpi=300, languages=["ko-KR", "en-US"])
        # Filter to data area: skip top header (y_top < 0.18) and bottom footer (y_top > 0.95)
        data_boxes = [b for b in boxes if 0.18 < b.y_top < 0.95]
        rows = group_into_rows(data_boxes, y_tolerance=0.012)
        for row in rows:
            cells: dict[str, list[str]] = {k: [] for k in COL_X_CENTERS}
            for b in row:
                col = assign_column(b.x_center)
                if col:
                    cells[col].append(b.text)
            session_txt = " ".join(cells["session"]).strip()
            subject = " ".join(cells["subject"]).strip()
            qnum_txt = " ".join(cells["qnum"]).strip()
            ans_txt = " ".join(cells["answer"]).strip()

            sm = SESSION_RE.match(session_txt)
            qm = NUMBER_ONLY.match(qnum_txt)
            am = NUMBER_ONLY.match(ans_txt)
            canon_subj = canonicalize_subject(subject)
            if not (sm and qm and am and canon_subj):
                continue
            ans_int = int(am.group(1))
            if not (1 <= ans_int <= 5):
                continue
            rows_out.append({
                "session": int(sm.group(1)),
                "subject": canon_subj,
                "qnum": int(qm.group(1)),
                "answer": ans_int,
            })
    return rows_out


def upsert_subject(conn, name: str, session: int) -> int:
    cur = conn.execute("SELECT id FROM subjects WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO subjects (name, session) VALUES (?, ?)", (name, session))
    return cur.lastrowid


def upsert_exam(conn, year: int, round_no: int, exam_date: str | None) -> int:
    cur = conn.execute("SELECT id FROM exams WHERE year = ? AND round = ?", (year, round_no))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO exams (year, round, exam_date) VALUES (?, ?, ?)",
        (year, round_no, exam_date),
    )
    return cur.lastrowid


def import_year(year: int) -> dict:
    if year not in EXAM_FOLDERS:
        raise ValueError(f"Unknown year {year}")
    pdf = find_answer_pdf(EXAM_FOLDERS[year])
    t0 = time.time()
    rows = parse_answer_pdf(pdf)
    init_db()
    counts: dict[str, int] = {}
    with get_conn() as conn:
        exam_id = upsert_exam(conn, year, ROUND_MAP[year], EXAM_DATE.get(year))
        for r in rows:
            subj_id = upsert_subject(conn, r["subject"], r["session"])
            conn.execute(
                """INSERT OR IGNORE INTO questions
                   (exam_id, subject_id, question_number, body, answer)
                   VALUES (?, ?, ?, '', ?)""",
                (exam_id, subj_id, r["qnum"], r["answer"]),
            )
            conn.execute(
                """UPDATE questions SET answer = ?
                   WHERE exam_id = ? AND subject_id = ? AND question_number = ?""",
                (r["answer"], exam_id, subj_id, r["qnum"]),
            )
            counts[r["subject"]] = counts.get(r["subject"], 0) + 1
    elapsed = time.time() - t0
    return {"pdf": str(pdf), "rows": len(rows), "by_subject": counts, "seconds": elapsed}


if __name__ == "__main__":
    target_years = [int(a) for a in sys.argv[1:]] or [2024]
    for y in target_years:
        result = import_year(y)
        print(f"[{y}] {result['rows']}문제 파싱 ({result['seconds']:.1f}s) — {Path(result['pdf']).name}")
        for subj, n in sorted(result["by_subject"].items()):
            print(f"  - {subj}: {n}")
