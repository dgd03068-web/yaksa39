"""Parse exam-question PDFs (image-based, 2-column layout) via OCR."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import EXAM_FOLDERS, ROUND_MAP  # noqa: E402
from db import get_conn, init_db  # noqa: E402
from parsers._ocr import nfc, ocr_pdf_page, group_into_rows, Box  # noqa: E402

# 시험지 파일명: 다양한 패턴 지원
#   "...N교시 기출문제..." (21~24년 형태)
#   "...N교시.pdf" (25년 간소 형태)
# 주의: "최종답안" 포함 파일은 답안 PDF이므로 제외
EXAM_FILE_RE = re.compile(r"(\d)\s*교시(?!.*최종답안).*\.pdf$")
ANSWER_EXCLUDE_RE = re.compile(r"최종답안")

# 좌우 컬럼 경계 (정규화 X)
COLUMN_SPLIT_X = 0.5

# 문제 시작: 숫자 + "." (1자리~3자리, 행 처음)
QUESTION_START = re.compile(r"^\s*(\d{1,3})\s*\.\s*(.*)$")
# 보기 시작: ①②③④⑤
CHOICE_MARKS = "①②③④⑤"
CHOICE_START = re.compile(r"^\s*([①②③④⑤])\s*(.*)$")
# 비공개 자료
HIDDEN_RE = re.compile(r"<\s*자료\s*\(\s*비공개\s*\)\s*>")
# 묶음 문제: [N~M] 또는 [N ~ M]
GROUP_RANGE_RE = re.compile(r"\[\s*(\d+)\s*~\s*(\d+)\s*\]")


def find_exam_pdfs(folder: Path) -> dict[int, Path]:
    """{session_no: pdf_path} for sessions 1..4 in a year folder."""
    out: dict[int, Path] = {}
    for f in folder.iterdir():
        m = EXAM_FILE_RE.search(nfc(f.name))
        if m:
            out[int(m.group(1))] = f
    return out


def split_columns(boxes: list[Box]) -> tuple[list[Box], list[Box]]:
    left = [b for b in boxes if b.x_center < COLUMN_SPLIT_X]
    right = [b for b in boxes if b.x_center >= COLUMN_SPLIT_X]
    return left, right


def boxes_to_lines(boxes: list[Box], y_tolerance: float = 0.008) -> list[str]:
    """Group boxes into rows top→bottom, join each row left→right with spaces."""
    rows = group_into_rows(boxes, y_tolerance=y_tolerance)
    return [" ".join(b.text for b in row) for row in rows]


def lines_in_reading_order(boxes: list[Box]) -> list[str]:
    """Left column lines (top→bottom) followed by right column lines."""
    left, right = split_columns(boxes)
    return boxes_to_lines(left) + boxes_to_lines(right)


def extract_questions_from_lines(lines: list[str]) -> list[dict]:
    """Walk lines, build questions when we see '<n>.'.
    Returns list of {qnum, body_lines, choices: {1..5: text}, has_image: bool}.
    """
    questions: list[dict] = []
    current: dict | None = None
    current_choice: int | None = None
    pending_group: tuple[int, int] | None = None  # [N~M] preamble pending attach

    def start_question(qnum: int, first_line: str):
        nonlocal current, current_choice
        current = {
            "qnum": qnum,
            "body_lines": [first_line] if first_line.strip() else [],
            "choices": {},
            "has_image": False,
        }
        current_choice = None
        questions.append(current)

    def append_to_current(line: str):
        if current is None:
            return
        if current_choice is not None:
            current["choices"][current_choice] += " " + line.strip()
        else:
            current["body_lines"].append(line)
        if HIDDEN_RE.search(line):
            current["has_image"] = True

    group_buffer: list[str] = []  # accumulator for [N~M] case body

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # 묶음 문제 시작
        gm = GROUP_RANGE_RE.search(line)
        if gm:
            pending_group = (int(gm.group(1)), int(gm.group(2)))
            group_buffer = []
            # 같은 라인의 [N~M] 뒤에 본문이 있을 수 있음
            after = line[gm.end():].strip()
            if after:
                group_buffer.append(after)
            current_choice = None
            continue
        # 보기 마커?
        cm = CHOICE_START.match(line)
        if cm and current is not None:
            mark, rest = cm.group(1), cm.group(2)
            current_choice = CHOICE_MARKS.index(mark) + 1
            current["choices"][current_choice] = rest.strip()
            continue
        # 새 문제 시작?
        qm = QUESTION_START.match(line)
        if qm:
            qnum = int(qm.group(1))
            start_question(qnum, qm.group(2))
            # 묶음 본문이 대기 중이고, 이 문제번호가 묶음 범위 안이면 prepend
            if pending_group and pending_group[0] <= qnum <= pending_group[1]:
                if group_buffer:
                    current["body_lines"] = list(group_buffer) + current["body_lines"]
                if qnum == pending_group[1]:
                    pending_group = None
                    group_buffer = []
            elif pending_group is None:
                pass
            # detect hidden in first line
            if HIDDEN_RE.search(qm.group(2)):
                current["has_image"] = True
            continue
        # 일반 라인
        if pending_group is not None and current is None:
            # 아직 첫 묶음 문제 시작 전: 묶음 본문 누적
            group_buffer.append(line)
        else:
            append_to_current(line)

    # 마무리: choices 딕셔너리 → 정렬, body_lines join
    out: list[dict] = []
    for q in questions:
        body = " ".join(s.strip() for s in q["body_lines"] if s.strip())
        out.append({
            "qnum": q["qnum"],
            "body": body,
            "has_image": 1 if q["has_image"] or HIDDEN_RE.search(body) else 0,
            "choices": [(n, q["choices"].get(n, "")) for n in (1, 2, 3, 4, 5)],
        })
    return out


def parse_exam_pdf(pdf_path: Path, dpi: int = 250) -> list[dict]:
    """OCR every page (skip cover page 0), return list of question dicts."""
    doc = fitz.open(pdf_path)
    n_pages = len(doc)
    doc.close()
    all_lines: list[str] = []
    # page 0 is the cover; data starts at page 1
    for page_idx in range(1, n_pages):
        boxes = ocr_pdf_page(pdf_path, page_idx, dpi=dpi)
        # Filter to data area (skip top header & bottom page-number)
        data_boxes = [b for b in boxes if 0.05 < b.y_top < 0.95]
        all_lines.extend(lines_in_reading_order(data_boxes))
        all_lines.append("")  # page break
    return extract_questions_from_lines(all_lines)


def import_exam_year(year: int) -> dict:
    if year not in EXAM_FOLDERS:
        raise ValueError(f"Unknown year {year}")
    pdfs = find_exam_pdfs(EXAM_FOLDERS[year])
    if not pdfs:
        raise FileNotFoundError(f"시험지 PDF를 찾을 수 없음: {EXAM_FOLDERS[year]}")

    init_db()
    summary: dict[int, int] = {}

    with get_conn() as conn:
        # exam_id 찾기 (답안 파서가 먼저 만들어야 함)
        row = conn.execute(
            "SELECT id FROM exams WHERE year = ? AND round = ?",
            (year, ROUND_MAP[year]),
        ).fetchone()
        if not row:
            raise RuntimeError("exams row 없음 — parse_answer.py 먼저 실행하세요")
        exam_id = row["id"]

        for session in sorted(pdfs):
            pdf = pdfs[session]
            t0 = time.time()
            qs = parse_exam_pdf(pdf)
            elapsed = time.time() - t0
            # session에 해당하는 subjects 조회
            subj_rows = conn.execute(
                "SELECT id, name FROM subjects WHERE session = ?", (session,)
            ).fetchall()
            if not subj_rows:
                print(f"[{session}교시] subject 없음 — answer parser 먼저 실행")
                continue

            inserted = 0
            for q in qs:
                # 어떤 과목 소속인지: 해당 session의 과목 후보 중 question_number 맞는 것
                # 임상실무2(1~63) + 보건법규(64~83)처럼 한 교시 안에 두 과목인 경우 처리
                subj_id = None
                for s in subj_rows:
                    cnt = conn.execute(
                        """SELECT COUNT(*) AS c FROM questions
                           WHERE exam_id = ? AND subject_id = ? AND question_number = ?""",
                        (exam_id, s["id"], q["qnum"]),
                    ).fetchone()["c"]
                    if cnt > 0:
                        subj_id = s["id"]
                        break
                if subj_id is None:
                    # 답안에 없는 문제(OCR 누락) → 첫 subject로 임시 배정
                    subj_id = subj_rows[0]["id"]
                    conn.execute(
                        """INSERT OR IGNORE INTO questions
                           (exam_id, subject_id, question_number, body, has_image)
                           VALUES (?, ?, ?, ?, ?)""",
                        (exam_id, subj_id, q["qnum"], q["body"], q["has_image"]),
                    )

                conn.execute(
                    """UPDATE questions SET body = ?, has_image = ?
                       WHERE exam_id = ? AND subject_id = ? AND question_number = ?""",
                    (q["body"], q["has_image"], exam_id, subj_id, q["qnum"]),
                )
                qid = conn.execute(
                    """SELECT id FROM questions
                       WHERE exam_id = ? AND subject_id = ? AND question_number = ?""",
                    (exam_id, subj_id, q["qnum"]),
                ).fetchone()
                if qid:
                    qid = qid["id"]
                    conn.execute("DELETE FROM choices WHERE question_id = ?", (qid,))
                    for n, text in q["choices"]:
                        conn.execute(
                            """INSERT INTO choices (question_id, number, text)
                               VALUES (?, ?, ?)""",
                            (qid, n, text or ""),
                        )
                    inserted += 1
            summary[session] = inserted
            print(f"[{session}교시] {inserted}문제 ({elapsed:.1f}s) — {pdf.name}")
    return {"by_session": summary}


if __name__ == "__main__":
    target_years = [int(a) for a in sys.argv[1:]] or [2024]
    for y in target_years:
        result = import_exam_year(y)
        print(f"[{y}] 완료:", result)
