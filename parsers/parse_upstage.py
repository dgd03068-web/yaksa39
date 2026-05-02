"""Upstage Document Parse API로 시험지·답안 PDF를 정확하게 재추출.

요구사항:
  - 환경변수 UPSTAGE_API_KEY (https://console.upstage.ai 에서 발급, 가입 즉시 100K 크레딧 무료)

흐름:
  1. 5개 PDF (4 시험지 + 1 답안) 각각 Upstage에 업로드
  2. JSON 응답에서 페이지별 텍스트/마크다운 추출
  3. 정규식으로 문제·보기·정답 파싱
  4. 기존 DB와 diff → audit 테이블에 변경 이력 기록 + questions 갱신
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import EXAM_FOLDERS, ROUND_MAP  # noqa: E402
from db import get_conn, init_db  # noqa: E402

UPSTAGE_URL = "https://api.upstage.ai/v1/document-ai/document-parse"

EXAM_FILE_RE = re.compile(r"(\d)\s*교시.*기출문제.*\.pdf$")
ANSWER_FILE_RE = re.compile(r"(?:1\s*~\s*4교시.*최종답안|최종답안).*\.pdf$")

QNUM_RE = re.compile(r"^\s*(\d{1,3})\.\s+(.+)$", re.MULTILINE)
CHOICE_MARKS = "①②③④⑤"
CHOICE_RE = re.compile(r"([①②③④⑤])\s*([^\n①②③④⑤]+)")
HIDDEN_RE = re.compile(r"<\s*자료\s*\(\s*비공개\s*\)\s*>|자료\s*\(?비공개\)?")

# 답안 표 한 행: "1교시 | 생명약학 | 1 | 4"
ANSWER_ROW_RE = re.compile(
    r"(\d)\s*교시\s*\|\s*([가-힣·\s]+?)\s*\|\s*(\d{1,3})\s*\|\s*([1-5])"
)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def call_upstage(pdf_path: Path, api_key: str) -> dict:
    """PDF 1개를 Upstage에 보내고 결과 JSON 반환."""
    with pdf_path.open("rb") as f:
        files = {"document": (pdf_path.name, f, "application/pdf")}
        data = {
            "model": "document-parse",
            "output_formats": '["markdown", "html", "text"]',
            "coordinates": "false",
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        r = requests.post(UPSTAGE_URL, headers=headers, files=files, data=data, timeout=300)
    r.raise_for_status()
    return r.json()


def extract_pages_text(upstage_resp: dict) -> list[str]:
    """Upstage 응답에서 페이지별 텍스트(혹은 마크다운) 리스트 추출."""
    pages_text: dict[int, list[str]] = {}
    elements = upstage_resp.get("elements", [])
    for el in elements:
        page = el.get("page", 1)
        # 우선 markdown -> html -> text 순으로 사용
        content = el.get("content", {})
        text = content.get("markdown") or content.get("text") or content.get("html") or ""
        if not text:
            text = el.get("text", "")
        if text:
            pages_text.setdefault(page, []).append(text.strip())
    if not pages_text:
        # 폴백: top-level content
        content = upstage_resp.get("content", {})
        md = content.get("markdown") or content.get("text") or ""
        if md:
            return [md]
    return [" \n ".join(pages_text[p]) for p in sorted(pages_text)]


def parse_exam_text(pages: list[str], session: int, year: int) -> list[dict]:
    """시험지 페이지 텍스트들을 받아 question dict list 반환."""
    full = "\n\n".join(pages)
    full = nfc(full)

    # "시험 종료" 같은 영역은 자르고
    full = re.sub(r"\d+\s*교시\s*종료", "", full)

    # 묶음 사례 [N~M] 처리: 사례 본문을 캡처해서 N~M 문제에 prepend
    grouped_cases: dict[tuple[int, int], str] = {}
    for m in re.finditer(r"\[(\d+)\s*~\s*(\d+)\]\s*다음 사례를 읽고[^\n]*\n+(.{0,1500}?)\n\s*\d+\.\s",
                         full, re.DOTALL):
        lo, hi = int(m.group(1)), int(m.group(2))
        case_text = m.group(3).strip()
        grouped_cases[(lo, hi)] = case_text

    # 문제 split: 각 문제는 "N. 본문 ... ① ... ② ... ⑤ ..."
    # 문제 앞 줄바꿈 패턴에 따라 분리
    parts = re.split(r"(?=^\s*\d{1,3}\.\s)", full, flags=re.MULTILINE)
    out: list[dict] = []
    for part in parts:
        m = re.match(r"\s*(\d{1,3})\.\s+(.+)", part, re.DOTALL)
        if not m:
            continue
        qnum = int(m.group(1))
        rest = m.group(2)

        # 보기 분리: ①~⑤
        choices_dict: dict[int, str] = {}
        # 본문 = 첫 ① 이전
        first_choice_match = re.search(r"[①②③④⑤]", rest)
        if first_choice_match:
            body = rest[:first_choice_match.start()].strip()
            choice_str = rest[first_choice_match.start():]
            for cm in CHOICE_RE.finditer(choice_str):
                mark, text = cm.group(1), cm.group(2).strip()
                idx = CHOICE_MARKS.index(mark) + 1
                # 같은 마크가 여러 번 나오면 첫 번째만 유지
                if idx not in choices_dict:
                    choices_dict[idx] = text
        else:
            body = rest.strip()

        # 묶음 사례 prepend
        for (lo, hi), case in grouped_cases.items():
            if lo <= qnum <= hi:
                body = f"{case}\n\n{body}"
                break

        has_image = bool(HIDDEN_RE.search(body))

        out.append({
            "qnum": qnum,
            "body": body,
            "has_image": 1 if has_image else 0,
            "choices": [(n, choices_dict.get(n, "")) for n in (1, 2, 3, 4, 5)],
        })
    return out


def parse_answer_text(pages: list[str]) -> list[dict]:
    """답안 페이지 텍스트들을 받아 [(session, subject, qnum, answer)] 반환."""
    full = nfc("\n".join(pages))
    out = []
    # Markdown 테이블 또는 plain "1교시 생명약학 3 4" 형태 둘 다 지원
    for m in ANSWER_ROW_RE.finditer(full):
        out.append({
            "session": int(m.group(1)),
            "subject": canonicalize_subject(m.group(2)),
            "qnum": int(m.group(3)),
            "answer": int(m.group(4)),
        })
    if not out:
        # space-separated fallback
        for m in re.finditer(r"(\d)\s*교시\s+([가-힣·\s]+?)\s+(\d{1,3})\s+([1-5])", full):
            out.append({
                "session": int(m.group(1)),
                "subject": canonicalize_subject(m.group(2)),
                "qnum": int(m.group(3)),
                "answer": int(m.group(4)),
            })
    return out


def canonicalize_subject(raw: str) -> str:
    key = re.sub(r"[·•·.\s]+", "", nfc(raw or ""))
    mapping = {
        "생명약학": "생명약학",
        "산업약학": "산업약학",
        "임상실무약학1": "임상·실무약학1",
        "임상실무약학2": "임상·실무약학2",
        "보건의약관계법규": "보건·의약관계법규",
    }
    return mapping.get(key, raw.strip())


def find_pdfs(year: int) -> tuple[Path | None, dict[int, Path]]:
    folder = EXAM_FOLDERS[year]
    answer_pdf = None
    exams: dict[int, Path] = {}
    for f in folder.iterdir():
        name = nfc(f.name)
        if ANSWER_FILE_RE.search(name):
            answer_pdf = f
            continue
        m = EXAM_FILE_RE.search(name)
        if m:
            exams[int(m.group(1))] = f
    return answer_pdf, exams


def import_year_via_upstage(year: int) -> dict:
    api_key = os.environ.get("UPSTAGE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "UPSTAGE_API_KEY 환경변수가 비어있습니다.\n"
            "  1) https://console.upstage.ai 에서 가입 (무료, Google 로그인 가능)\n"
            "  2) 'API Keys' 메뉴 → 'Generate New Key'\n"
            "  3) 터미널에 `export UPSTAGE_API_KEY=\"up_xxx\"` 후 이 스크립트 재실행"
        )

    init_db()
    answer_pdf, exam_pdfs = find_pdfs(year)
    if not answer_pdf:
        raise FileNotFoundError(f"답안 PDF 없음: {EXAM_FOLDERS[year]}")
    if not exam_pdfs:
        raise FileNotFoundError(f"시험지 PDF 없음: {EXAM_FOLDERS[year]}")

    summary = {"answers": 0, "questions": 0, "by_session": {}, "audit_changes": 0}

    # ---- 1) 답안 PDF ----
    print(f"[답안] {answer_pdf.name} 업로드 중...")
    t0 = time.time()
    resp = call_upstage(answer_pdf, api_key)
    pages = extract_pages_text(resp)
    rows = parse_answer_text(pages)
    print(f"  {len(rows)}개 답안 추출 ({time.time()-t0:.1f}s)")
    summary["answers"] = len(rows)

    with get_conn() as conn:
        # exam row 보장
        cur = conn.execute(
            "INSERT OR IGNORE INTO exams (year, round) VALUES (?, ?)",
            (year, ROUND_MAP[year]),
        )
        exam_id = conn.execute(
            "SELECT id FROM exams WHERE year=? AND round=?", (year, ROUND_MAP[year])
        ).fetchone()["id"]
        for r in rows:
            # subject
            sb = conn.execute("SELECT id FROM subjects WHERE name=?", (r["subject"],)).fetchone()
            if not sb:
                conn.execute(
                    "INSERT INTO subjects (name, session) VALUES (?, ?)",
                    (r["subject"], r["session"]),
                )
                sb = conn.execute("SELECT id FROM subjects WHERE name=?", (r["subject"],)).fetchone()
            subj_id = sb["id"]
            # 기존 정답
            existing = conn.execute(
                "SELECT id, answer FROM questions WHERE exam_id=? AND subject_id=? AND question_number=?",
                (exam_id, subj_id, r["qnum"]),
            ).fetchone()
            if existing:
                if existing["answer"] != r["answer"]:
                    conn.execute(
                        """INSERT INTO audit (question_id, field, old_value, new_value, source, confidence, resolved_at)
                           VALUES (?, 'answer', ?, ?, 'upstage', 0.95, ?)""",
                        (existing["id"], str(existing["answer"]), str(r["answer"]),
                         time.strftime("%Y-%m-%dT%H:%M:%S")),
                    )
                    summary["audit_changes"] += 1
                conn.execute("UPDATE questions SET answer=? WHERE id=?", (r["answer"], existing["id"]))
            else:
                conn.execute(
                    """INSERT INTO questions (exam_id, subject_id, question_number, body, answer)
                       VALUES (?, ?, ?, '', ?)""",
                    (exam_id, subj_id, r["qnum"], r["answer"]),
                )

    # ---- 2) 시험지 PDF (교시별) ----
    for session in sorted(exam_pdfs):
        pdf = exam_pdfs[session]
        print(f"[{session}교시] {pdf.name} 업로드 중...")
        t0 = time.time()
        resp = call_upstage(pdf, api_key)
        pages = extract_pages_text(resp)
        questions = parse_exam_text(pages, session, year)
        print(f"  {len(questions)}문제 추출 ({time.time()-t0:.1f}s)")
        summary["by_session"][session] = len(questions)
        summary["questions"] += len(questions)

        with get_conn() as conn:
            exam_id = conn.execute(
                "SELECT id FROM exams WHERE year=? AND round=?", (year, ROUND_MAP[year])
            ).fetchone()["id"]
            subj_rows = conn.execute(
                "SELECT id, name FROM subjects WHERE session=?", (session,)
            ).fetchall()
            if not subj_rows:
                print(f"  ⚠ session={session} 과목 없음 — skip")
                continue
            for q in questions:
                # 어떤 과목인지 매칭 (exam_id+subject+qnum 존재 확인)
                subj_id = None
                for s in subj_rows:
                    cnt = conn.execute(
                        "SELECT COUNT(*) c FROM questions WHERE exam_id=? AND subject_id=? AND question_number=?",
                        (exam_id, s["id"], q["qnum"]),
                    ).fetchone()["c"]
                    if cnt > 0:
                        subj_id = s["id"]
                        break
                if subj_id is None:
                    subj_id = subj_rows[0]["id"]

                existing = conn.execute(
                    "SELECT id, body, has_image FROM questions WHERE exam_id=? AND subject_id=? AND question_number=?",
                    (exam_id, subj_id, q["qnum"]),
                ).fetchone()
                if existing:
                    qid = existing["id"]
                    # body diff → audit
                    if (existing["body"] or "").strip() != q["body"].strip():
                        conn.execute(
                            """INSERT INTO audit (question_id, field, old_value, new_value, source, confidence, resolved_at)
                               VALUES (?, 'body', ?, ?, 'upstage', 0.9, ?)""",
                            (qid, (existing["body"] or "")[:500], q["body"][:500], "upstage",
                             time.strftime("%Y-%m-%dT%H:%M:%S")),
                        )
                        summary["audit_changes"] += 1
                    conn.execute(
                        "UPDATE questions SET body=?, has_image=? WHERE id=?",
                        (q["body"], q["has_image"], qid),
                    )
                else:
                    cur = conn.execute(
                        """INSERT INTO questions (exam_id, subject_id, question_number, body, has_image)
                           VALUES (?, ?, ?, ?, ?)""",
                        (exam_id, subj_id, q["qnum"], q["body"], q["has_image"]),
                    )
                    qid = cur.lastrowid
                # 보기 갱신 (replace)
                conn.execute("DELETE FROM choices WHERE question_id=?", (qid,))
                for n, text in q["choices"]:
                    conn.execute(
                        "INSERT INTO choices (question_id, number, text) VALUES (?, ?, ?)",
                        (qid, n, text or ""),
                    )

    return summary


# audit 테이블이 없으면 생성
def ensure_audit_table():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY,
              question_id INTEGER REFERENCES questions(id) ON DELETE CASCADE,
              field TEXT NOT NULL,
              old_value TEXT,
              new_value TEXT,
              source TEXT NOT NULL,
              confidence REAL,
              resolved_at TEXT NOT NULL
            )
        """)


if __name__ == "__main__":
    ensure_audit_table()
    target_years = [int(a) for a in sys.argv[1:]] or [2024]
    for y in target_years:
        result = import_year_via_upstage(y)
        print(f"\n[{y}] 완료: 답안 {result['answers']}, 시험지 {result['questions']}, 변경 {result['audit_changes']}")
        for s, n in sorted(result["by_session"].items()):
            print(f"  - {s}교시: {n}문제")
