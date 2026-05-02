"""Claude API로 각 question을 chapter에 분류한다.

규칙
- 1교시(생명약학): 생화학/분자생물학/미생물학/면역학/약리학(기초)/인체병태생리/예방약학·보건학/독성학 중 하나
- 2교시(산업약학): 약제학/물리약학/약품화학·의약화학/약품분석학/생약·천연물 중 하나
- 3교시·4교시(임상·실무약학1·2): 약리학 또는 약물치료학 단원(질환별 56개) 중 하나, 또는 약사실무·사회약학
- 4교시 보건·의약관계법규: 분류 안 함 (chapter_id NULL 유지)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_conn  # noqa: E402

CLIENT = anthropic.Anthropic()
MODEL = "claude-sonnet-4-5-20250929"  # 정확도 우선

BATCH_SIZE = 20  # 한 번에 N문제씩 분류 요청


def build_taxonomy(conn) -> dict:
    """과목별 가능한 chapter 목록 (Claude에 전달).
    가상 subject는 제외 — 약치 단원은 별도로 임상실무약학 후보에 추가.
    """
    out: dict[str, list[dict]] = {}
    rows = conn.execute("""
        SELECT s.name AS subject, s.session,
               c.id AS chapter_id, c.name AS chapter_name
        FROM chapters c
        JOIN subjects s ON c.subject_id = s.id
        WHERE s.name != '약물치료학(가상)'
          AND c.parent_id IS NULL
        ORDER BY s.session, s.id, c.chapter_no NULLS LAST
    """).fetchall()
    for r in rows:
        out.setdefault(r["subject"], []).append({
            "id": r["chapter_id"],
            "name": r["chapter_name"],
            "category": None,
        })
    return out


def fetch_unclassified(conn, year: int, subject_id: int | None = None, limit: int = None) -> list[dict]:
    sql = """
      SELECT q.id, q.subject_id, s.name AS subject, s.session,
             q.question_number AS qnum, q.body
      FROM questions q
      JOIN exams e ON q.exam_id = e.id
      JOIN subjects s ON q.subject_id = s.id
      WHERE e.year = ?
        AND q.chapter_id IS NULL
        AND s.name != '보건·의약관계법규'
        AND s.name != '약물치료학(가상)'
    """
    params: list = [year]
    if subject_id:
        sql += " AND q.subject_id = ?"
        params.append(subject_id)
    sql += " ORDER BY s.session, s.id, q.question_number"
    if limit:
        sql += f" LIMIT {limit}"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def classify_batch(subject: str, chapters_for_subject: list[dict], questions: list[dict]) -> dict[int, int]:
    """한 과목의 N문제를 한 번에 Claude에게 분류 요청. 반환: {qid: chapter_id}."""

    chapter_lines = []
    for c in chapters_for_subject:
        cat = f" ({c['category']})" if c.get("category") else ""
        chapter_lines.append(f"  - id={c['id']}: {c['name']}{cat}")
    chapter_text = "\n".join(chapter_lines)

    items_lines = []
    for q in questions:
        body_short = (q["body"] or "")[:600].replace("\n", " ")
        items_lines.append(f"  qid={q['id']} (문제 {q['qnum']}번): {body_short}")
    items_text = "\n".join(items_lines)

    prompt = f"""한국 약사 국가시험 [{subject}] 과목 문제들을 아래 단원 목록에서 가장 적합한 단원에 1개씩 분류해 주세요.

가능한 단원 (id로 매핑):
{chapter_text}

분류 대상 문제들:
{items_text}

각 문제를 가장 적합한 단원에 분류하고, JSON 객체로 반환하세요. key는 qid(숫자), value는 chapter id(숫자).
정말 어떤 단원에도 안 맞으면 value는 null. JSON만 출력, 다른 설명 없이.

예: {{"123": 5, "124": 7, "125": null}}"""

    msg = CLIENT.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    # extract JSON
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        raw = json.loads(text[start:end + 1])
        return {int(k): (int(v) if v is not None else None) for k, v in raw.items()}
    except Exception as e:
        print(f"  JSON 파싱 실패: {e}\n  text: {text[:200]}")
        return {}


def classify_year(year: int, subject_filter: list[str] | None = None) -> dict:
    """연도의 모든 unclassified question에 chapter_id 할당."""
    with get_conn() as conn:
        taxonomy = build_taxonomy(conn)

        # 약물치료학(가상)의 leaf 단원만 임상·실무약학1·2 후보에 추가
        # (root='약물치료학', 카테고리는 parent로만 사용. leaf = chapter_no IS NOT NULL)
        drug_chapters = []
        rows = conn.execute("""
            SELECT c.id, c.name, p.name AS category
            FROM chapters c
            JOIN chapters p ON c.parent_id = p.id
            JOIN subjects s ON c.subject_id = s.id
            WHERE s.name = '약물치료학(가상)'
              AND c.chapter_no IS NOT NULL
              AND p.name != '약물치료학'
            ORDER BY p.name, c.chapter_no
        """).fetchall()
        for r in rows:
            drug_chapters.append({"id": r["id"], "name": r["name"], "category": r["category"]})

        # 임상실무약학1, 2 taxonomy에 약치 단원 추가
        for k in ["임상·실무약학1", "임상·실무약학2"]:
            taxonomy.setdefault(k, []).extend(drug_chapters)

        subjects_with_questions = conn.execute("""
            SELECT DISTINCT s.id, s.name FROM questions q
            JOIN exams e ON q.exam_id = e.id
            JOIN subjects s ON q.subject_id = s.id
            WHERE e.year = ? AND s.name != '보건·의약관계법규' AND s.name != '약물치료학(가상)'
            ORDER BY s.session, s.id
        """, (year,)).fetchall()

        total = 0
        updated = 0
        per_subject: dict[str, int] = {}

        for srow in subjects_with_questions:
            sname = srow["name"]
            if subject_filter and sname not in subject_filter:
                continue
            chapters_here = taxonomy.get(sname, [])
            if not chapters_here:
                print(f"⚠ [{sname}] taxonomy 없음 — skip")
                continue
            qs = fetch_unclassified(conn, year, subject_id=srow["id"])
            print(f"[{sname}] {len(qs)}문제 분류 시작 ({len(chapters_here)}개 단원 후보)")
            total += len(qs)

            for i in range(0, len(qs), BATCH_SIZE):
                batch = qs[i:i + BATCH_SIZE]
                t0 = time.time()
                mapping = classify_batch(sname, chapters_here, batch)
                elapsed = time.time() - t0
                hit = 0
                for q in batch:
                    cid = mapping.get(q["id"])
                    if cid is not None:
                        conn.execute("UPDATE questions SET chapter_id = ? WHERE id = ?", (cid, q["id"]))
                        hit += 1
                conn.commit()
                updated += hit
                per_subject[sname] = per_subject.get(sname, 0) + hit
                print(f"  · 배치 {i//BATCH_SIZE + 1}: {hit}/{len(batch)} 매칭 ({elapsed:.1f}s)")

        return {"total": total, "updated": updated, "per_subject": per_subject}


if __name__ == "__main__":
    args = sys.argv[1:]
    year = int(args[0]) if args else 2024
    subject_filter = args[1:] if len(args) > 1 else None
    res = classify_year(year, subject_filter)
    print(f"\n총 {res['updated']}/{res['total']} 분류")
    for s, n in res["per_subject"].items():
        print(f"  - {s}: {n}")
