"""해설 자동생성 스크립트 — Claude API (requests 사용, anthropic SDK 불필요)

사용법:
    ANTHROPIC_API_KEY=sk-ant-... python generate_explanations.py --db /path/to/questions.db
    ANTHROPIC_API_KEY=sk-ant-... python generate_explanations.py --db /path/to/questions.db --limit 10
    ANTHROPIC_API_KEY=sk-ant-... python generate_explanations.py --db /path/to/questions.db --subject 2
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROGRESS_FILE = SCRIPT_DIR / "_explanation_progress.json"

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-5-20250929"
BATCH_SIZE = 5
MAX_TOKENS = 8192
RETRY_LIMIT = 3
RETRY_DELAY = 5
RATE_LIMIT_DELAY = 1.5


def get_conn(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return set(json.load(f).get("completed", []))
    return set()


def save_progress(completed):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"completed": sorted(completed)}, f)


def fetch_questions(conn, subject_id=None, limit=None, skip_ids=None):
    sql = """
        SELECT q.id, q.exam_id, q.subject_id, q.question_number,
               q.body, q.answer, q.chapter_id,
               e.year, e.round,
               s.name AS subject_name,
               c.name AS chapter_name
        FROM questions q
        JOIN exams e ON q.exam_id = e.id
        JOIN subjects s ON q.subject_id = s.id
        LEFT JOIN chapters c ON q.chapter_id = c.id
        WHERE (q.explanation IS NULL OR q.explanation = '')
          AND q.body IS NOT NULL AND LENGTH(q.body) > 5
    """
    params = []
    if subject_id:
        sql += " AND q.subject_id = ?"
        params.append(subject_id)
    sql += " ORDER BY e.year, s.session, q.question_number"
    if limit:
        sql += f" LIMIT {limit + 200}"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if skip_ids:
        rows = [r for r in rows if r["id"] not in skip_ids]
    return rows[:limit] if limit else rows


def fetch_choices(conn, qid):
    return [dict(r) for r in conn.execute(
        "SELECT number, text FROM choices WHERE question_id=? ORDER BY number", (qid,)
    ).fetchall()]


def build_prompt(questions, all_choices):
    circled = ['①','②','③','④','⑤']
    items = []
    for q in questions:
        choices = all_choices.get(q["id"], [])
        clines = []
        for c in choices:
            m = circled[c["number"]-1] if c["number"] <= 5 else f"{c['number']})"
            clines.append(f"  {m} {c['text'] or '(빈 보기)'}")
        ct = "\n".join(clines)
        yr = f"{q['year']}년 {q['round']}회" if q.get('round') else f"{q['year']}년"
        ch = f" [{q['chapter_name']}]" if q.get('chapter_name') else ""
        sn = q.get('subject_name', '')
        items.append(
            f"### QID={q['id']} | {yr} {sn}{ch} | 문제 {q['question_number']}번 | 정답: {q['answer']}\n{q['body']}\n{ct}"
        )
    return f"""당신은 한국 약사 국가시험 해설 전문가입니다.
아래 문제들에 대해 각각 상세한 해설을 작성해 주세요.

각 해설 형식:
1. "정답: ③ [정답 보기 내용]" (정답 번호에 맞는 원문자)
2. [해설] 왜 정답인지 간결 설명
3. 각 보기 분석 (✅ 정답, 나머지는 왜 틀린지)
4. [핵심] 1~2줄 기억 포인트

반환: JSON 객체. key=qid(문자열), value=해설 문자열. JSON만 출력. 코드블록 없이.

{chr(10).join(items)}"""


def call_claude(api_key, prompt):
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    for attempt in range(RETRY_LIMIT):
        try:
            r = requests.post(API_URL, headers=headers, json=body, timeout=120)
            if r.status_code == 429:
                wait = RETRY_DELAY * (attempt + 2)
                print(f"  [RATE LIMIT] {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            text = data["content"][0]["text"].strip()
            s = text.find("{")
            e = text.rfind("}")
            if s == -1 or e == -1:
                print(f"  [WARN] No JSON (attempt {attempt+1})")
                continue
            raw = json.loads(text[s:e+1])
            return {int(k): str(v) for k, v in raw.items() if v}
        except json.JSONDecodeError as ex:
            print(f"  [JSON ERR] {ex} (attempt {attempt+1})")
            if attempt < RETRY_LIMIT - 1: time.sleep(RETRY_DELAY)
        except Exception as ex:
            print(f"  [ERR] {ex} (attempt {attempt+1})")
            if attempt < RETRY_LIMIT - 1: time.sleep(RETRY_DELAY)
    return {}


def save_explanations(conn, explanations):
    for qid, expl in explanations.items():
        conn.execute(
            "UPDATE questions SET explanation=?, explanation_source='ai-auto-gen' WHERE id=?",
            (expl, qid),
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="약사 국시 해설 자동생성")
    parser.add_argument("--db", type=str, required=True, help="questions.db 경로")
    parser.add_argument("--subject", type=int, help="과목 ID (1~5)")
    parser.add_argument("--limit", type=int, help="최대 문제 수")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--reset", action="store_true", help="진행 초기화")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY 환경변수 필요")
        sys.exit(1)

    if args.reset and PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
        print("진행 초기화됨.")

    completed = load_progress()
    print(f"이전 진행: {len(completed)}개 완료")

    conn = get_conn(db_path)
    questions = fetch_questions(conn, args.subject, args.limit, completed)

    if not questions:
        print("해설 필요한 문제 없음!")
        return

    print(f"대상: {len(questions)}개 | 배치: {args.batch_size}")
    total_b = (len(questions) + args.batch_size - 1) // args.batch_size
    print(f"배치 수: {total_b}\n")

    all_choices = {q["id"]: fetch_choices(conn, q["id"]) for q in questions}
    gen = 0

    for i in range(0, len(questions), args.batch_size):
        batch = questions[i:i+args.batch_size]
        bn = i // args.batch_size + 1
        qids = [q["id"] for q in batch]
        print(f"[{bn}/{total_b}] QIDs: {qids}")

        results = call_claude(api_key, build_prompt(batch, all_choices))
        if results:
            save_explanations(conn, results)
            completed.update(results.keys())
            save_progress(completed)
            gen += len(results)
            print(f"  OK {len(results)}/{len(batch)} (누적: {gen})")
        else:
            print(f"  FAIL")

        if i + args.batch_size < len(questions):
            time.sleep(RATE_LIMIT_DELAY)

    conn.close()
    print(f"\n=== 완료: {gen}개 생성, 누적 {len(completed)}개 ===")


if __name__ == "__main__":
    main()
