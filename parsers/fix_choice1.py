"""macOS Vision OCR이 ①번 보기를 본문 끝에 합쳐 출력하는 오류 후처리.

증상:
  body = "...적절한 약물요법은? l 라미프릴(ramipril)"
  choices[1] = ""  (비어있음)
  choices[2..5] = 정상

원인: ①가 'l', 'I', '1', '|', 'i' 등으로 OCR되고 본문 끝에 붙음.

수정 전략:
  1. choice 1이 비어있는 question 후보 fetch
  2. body 끝에서 단일 마커(l/I/1/|/i/①) + 공백 + 텍스트 패턴 감지
  3. 본문에서 분리, choice 1에 채움
  4. audit 기록

보수적 접근: 명확한 패턴만 분리. 애매하면 skip.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH  # noqa: E402

# ① 자리에 들어오는 OCR 오인식 문자: l, I, 1, |, i, ①, ⓛ
# 패턴: 본문 끝부분이 "?...구두점" 또는 "...있다." 다음 + 공백 + (l|I|1|...) + 공백 + 보기 텍스트
# 예: "...적절한 약물요법은? l 라미프릴(ramipril)"
# 예: "...uncoupling을 일으키는 것은? l antimycin A"
# 예: "...것은? 1 콜라겐(collagen)"

# 마커: 단독으로 등장하는 'l', 'I', '1', '|', 'i', '①', '⓵'
# 단어 경계 처리: 양쪽이 공백이거나 시작/끝
CHOICE1_MARKERS = ['①', 'ⓛ', '⓵']
# OCR 오인식 (제한적 — 너무 흐릿하면 false positive)
OCR_MARKERS_AMBIG = ['l', 'I', '1', '|', 'i']

# 분리 패턴: (?:body) (마커) (보기 텍스트)
# 마지막 ? 또는 . 또는 ) 또는 ] 또는 ; 또는 :)" 다음 마커
# Avoid: 본문 안의 "1차 치료제" 같은 숫자 표현 (1 다음 한글이 바로 붙음)
# Marker 뒤에 반드시 공백+텍스트 와야 분리

# 명확 케이스: body가 "?" 또는 "." 또는 ")" 직후 마커 + 공백 + 텍스트
SPLIT_RE = re.compile(
    r"^(?P<body>.*[?.\)\]>])\s+(?P<marker>[lI1\|i①ⓛ⓵])\s+(?P<choice>[^?]{2,200})$",
    re.DOTALL,
)


def try_split(body: str) -> tuple[str, str] | None:
    """본문 끝의 ①번 보기 분리. (new_body, choice1_text) 또는 None."""
    if not body:
        return None
    body = body.strip()
    m = SPLIT_RE.match(body)
    if not m:
        return None
    new_body = m.group("body").strip()
    choice = m.group("choice").strip()
    # 너무 짧은 보기는 의심 — 단어 한 개 이하
    if len(choice) < 2:
        return None
    # 보기 안에 다음 마커(②③④⑤)가 있으면 잘못된 분리
    if any(m in choice for m in "②③④⑤"):
        return None
    # 보기가 너무 길면 (200자 초과) 본문일 가능성
    if len(choice) > 200:
        return None
    return new_body, choice


def run() -> dict:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    ts = dt.datetime.now().isoformat(timespec="seconds")

    # 후보: choice 1이 비어있거나 없는 질문
    candidates = conn.execute("""
        SELECT q.id AS qid, q.body, c1.text AS c1_text
        FROM questions q
        LEFT JOIN choices c1 ON c1.question_id = q.id AND c1.number = 1
        WHERE q.body != ''
          AND (c1.text IS NULL OR c1.text = '' OR length(trim(c1.text)) < 2)
    """).fetchall()

    fixed = 0
    skipped = 0
    for r in candidates:
        result = try_split(r["body"])
        if not result:
            skipped += 1
            continue
        new_body, choice1 = result
        # audit
        conn.execute("""INSERT INTO audit (question_id, field, old_value, new_value, source, confidence, resolved_at)
                        VALUES (?, 'body+choice1', ?, ?, 'fix_choice1', 0.85, ?)""",
                     (r["qid"], (r["body"] or '')[:300], f"body={new_body[-100:]} | c1={choice1[:80]}", ts))
        conn.execute("UPDATE questions SET body=? WHERE id=?", (new_body, r["qid"]))
        # choice 1: insert or update
        existing_c1 = conn.execute("SELECT id FROM choices WHERE question_id=? AND number=1", (r["qid"],)).fetchone()
        if existing_c1:
            conn.execute("UPDATE choices SET text=? WHERE id=?", (choice1, existing_c1["id"]))
        else:
            conn.execute("INSERT INTO choices (question_id, number, text) VALUES (?, 1, ?)", (r["qid"], choice1))
        fixed += 1

    conn.commit()
    conn.close()
    return {"candidates": len(candidates), "fixed": fixed, "skipped": skipped}


if __name__ == "__main__":
    r = run()
    print(f"후보: {r['candidates']} / 분리 적용: {r['fixed']} / 패턴 안 맞음: {r['skipped']}")
