"""study_plan 항목 → 5년 기출 qid 리스트 변환 헬퍼.

study_plan 카테고리 4종 매핑:
- 약치: chapter_id 직접 → 해당 단원 모든 문제
- 합성: content를 쉼표 split → 약물명을 body/choices/concept_tag LIKE (의약화학 subject_id=2 범위)
- 생약: 동일 패턴, 생약학 chapter(149-153) 우선
- 약법: chapter_id 없음 → 약사법규 6단원(96-101) round-robin (날짜 offset % 6)
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import get_conn  # noqa: E402

LAW_CHAPTERS = [96, 97, 98, 99, 100, 101]  # 약사법규 sub-chapter 6단원
LAW_BASE_DATE = dt.date(2026, 8, 4)         # round-robin 기준일
HERB_CHAPTERS = [149, 150, 151, 152, 153]   # 생약학 sub-chapter (Tab 2 우선 범위)
SYNTH_CHAPTERS = [113, 114, 115, 116, 117, 118]  # 의약화학 sub-chapter


def _split_content(content: str) -> list[str]:
    """content (예: "Ketamine, Etomidate" or "갈근,감초,길경") → 토큰 리스트.
    2자 미만 토큰 skip."""
    if not content:
        return []
    parts = re.split(r"[,，·/]+", content)
    return [p.strip() for p in parts if len(p.strip()) >= 2]


def _search_questions_by_text(token: str, restrict_chapter_ids: list[int]) -> list[int]:
    """주어진 토큰을 body·choices.text·concept_tag에서 검색 → qid 리스트.
    토큰이 짧을 때 false positive 방지: chapter 범위로 제한."""
    if not token or len(token) < 2:
        return []
    pat = f"%{token}%"
    placeholders = ",".join("?" * len(restrict_chapter_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT q.id
            FROM questions q
            LEFT JOIN choices c ON c.question_id = q.id
            WHERE q.chapter_id IN ({placeholders})
              AND q.is_skipped = 0
              AND (q.body LIKE ? OR c.text LIKE ? OR q.concept_tag LIKE ?)
            """,
            (*restrict_chapter_ids, pat, pat, pat),
        ).fetchall()
    return [r["id"] for r in rows]


def _qids_by_chapter(chapter_id: int) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM questions WHERE chapter_id=? AND is_skipped=0",
            (chapter_id,),
        ).fetchall()
    return [r["id"] for r in rows]


def _law_chapter_for_date(plan_date: str) -> int:
    """plan_date를 6단원 round-robin 인덱스로 변환 → chapter_id."""
    try:
        d = dt.date.fromisoformat(plan_date)
    except Exception:
        return LAW_CHAPTERS[0]
    offset = (d - LAW_BASE_DATE).days % 6
    return LAW_CHAPTERS[offset]


def plan_items_to_qids(plan_date):
    """plan_date(str) 또는 (from_str, to_str) 기간 → 카테고리별 qid 리스트.
    반환: {"합성": [...], "생약": [...], "약치": [...], "약법": [...]}"""
    if isinstance(plan_date, (tuple, list)) and len(plan_date) == 2:
        date_clause = "plan_date BETWEEN ? AND ?"
        params: tuple = (plan_date[0], plan_date[1])
        date_list = _dates_in_range(plan_date[0], plan_date[1])
    else:
        date_clause = "plan_date = ?"
        params = (plan_date,)
        date_list = [plan_date]

    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT plan_date, category, content, chapter_id FROM study_plan WHERE {date_clause}",
            params,
        ).fetchall()

    result = {"합성": set(), "생약": set(), "약치": set(), "약법": set()}

    for r in rows:
        cat = r["category"]
        content = r["content"] or ""
        chap_id = r["chapter_id"]

        if cat == "약치" and chap_id is not None:
            result["약치"].update(_qids_by_chapter(chap_id))
        elif cat == "합성":
            for tok in _split_content(content):
                result["합성"].update(_search_questions_by_text(tok, SYNTH_CHAPTERS))
        elif cat == "생약":
            for tok in _split_content(content):
                result["생약"].update(_search_questions_by_text(tok, HERB_CHAPTERS))
        elif cat == "약법":
            law_cid = _law_chapter_for_date(r["plan_date"])
            result["약법"].update(_qids_by_chapter(law_cid))

    return {k: sorted(v) for k, v in result.items()}


def _dates_in_range(start: str, end: str) -> list[str]:
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    return [(s + dt.timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]


def study_aid_for_plan(plan_date) -> dict:
    """그날의 학습 노트 카드 (PDF 첫 페이지용).

    반환:
      {"합성": [{name, drug_class?, desc?}, ...],
       "생약": [{name}, ...],
       "약치": [{chapter_name, summary}],
       "약법": [{chapter_name, summary}]}
    """
    from parsers.study_notes_loader import get_chapter_note  # 지연 import

    if isinstance(plan_date, (tuple, list)) and len(plan_date) == 2:
        date_clause = "plan_date BETWEEN ? AND ?"
        params: tuple = (plan_date[0], plan_date[1])
    else:
        date_clause = "plan_date = ?"
        params = (plan_date,)

    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT plan_date, category, content, chapter_id FROM study_plan WHERE {date_clause}",
            params,
        ).fetchall()

    aid = {"합성": [], "생약": [], "약치": [], "약법": []}
    seen_law_chapters = set()  # 같은 약법 단원 중복 카드 방지

    with get_conn() as conn:
        for r in rows:
            cat = r["category"]
            content = (r["content"] or "").strip()
            chap_id = r["chapter_id"]

            if cat == "합성":
                for tok in _split_content(content):
                    drug = conn.execute(
                        "SELECT name_ko, name_en, drug_class, description "
                        "FROM drugs WHERE name_en LIKE ? OR name_ko LIKE ? LIMIT 1",
                        (f"%{tok}%", f"%{tok}%"),
                    ).fetchone()
                    if drug:
                        aid["합성"].append({
                            "name": f"{drug['name_ko'] or tok} ({drug['name_en']})",
                            "drug_class": drug["drug_class"] or "",
                            "desc": drug["description"] or "",
                        })
                    else:
                        aid["합성"].append({"name": tok})

            elif cat == "생약":
                for tok in _split_content(content):
                    aid["생약"].append({"name": tok})

            elif cat == "약치" and chap_id is not None:
                chap = conn.execute(
                    "SELECT name FROM chapters WHERE id=?", (chap_id,)
                ).fetchone()
                summary = get_chapter_note(chap_id, max_chars=350)
                aid["약치"].append({
                    "chapter_name": chap["name"] if chap else content,
                    "summary": summary,
                })

            elif cat == "약법":
                law_cid = _law_chapter_for_date(r["plan_date"])
                if law_cid in seen_law_chapters:
                    continue
                seen_law_chapters.add(law_cid)
                chap = conn.execute(
                    "SELECT name FROM chapters WHERE id=?", (law_cid,)
                ).fetchone()
                summary = get_chapter_note(law_cid, max_chars=350)
                aid["약법"].append({
                    "chapter_name": chap["name"] if chap else "약사법규",
                    "summary": summary,
                })

    # 카테고리당 최대 8개 카드 제한 (첫 페이지 overflow 방지)
    for cat in aid:
        aid[cat] = aid[cat][:8]
    return aid


def count_summary(qids_by_cat: dict) -> dict:
    """카테고리별 문제 수 + 합집합 크기."""
    union: set = set()
    counts = {}
    for cat, qids in qids_by_cat.items():
        counts[cat] = len(qids)
        union.update(qids)
    counts["_total_union"] = len(union)
    return counts


if __name__ == "__main__":
    # 단위 테스트
    r = plan_items_to_qids("2026-08-04")
    print("2026-08-04:", count_summary(r))
    for cat, qids in r.items():
        print(f"  {cat}: {len(qids)}개 — sample {qids[:5]}")
