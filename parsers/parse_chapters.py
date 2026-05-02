"""사용자 명시 15개 세부 과목을 chapter 트리로 시드한다.

사용자 정의 15개 (2026-05-02):
  생화학, 미생물학, 병태생리학, 예방약학, 약물학,
  약제학, 물리약학, 약품분석학, 약물합성학, 생약학, 한약제제학,
  약물치료학 (질환별 56단원 sub-tree),
  약국실무, 사회약학, 약사법규
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ROOT  # noqa: E402
from db import get_conn, init_db  # noqa: E402

LECTURE_DIR = ROOT / "강의자료" / "약물치료학"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


# ─────────────────────────────────────────────────────────────
# 사용자 정의 15개 세부 과목 → 시험 교시(subject) 매핑
# ─────────────────────────────────────────────────────────────
USER_SUBJECTS_15 = [
    # (chapter_name, owning subject, order)
    # 1교시 생명약학
    ("생화학",       "생명약학",          10),
    ("미생물학",     "생명약학",          20),
    ("병태생리학",   "생명약학",          30),
    ("예방약학",     "생명약학",          40),
    ("약물학",       "생명약학",          50),
    # 2교시 산업약학
    ("약제학",       "산업약학",          10),
    ("물리약학",     "산업약학",          20),
    ("약품분석학",   "산업약학",          30),
    ("약물합성학",   "산업약학",          40),
    ("생약학",       "산업약학",          50),
    ("한약제제학",   "산업약학",          60),
    # 3·4교시 임상·실무약학 — 약물치료학은 별도 가상 subject 아래 트리
    ("약물치료학",   "약물치료학(가상)",   10),
    ("약국실무",     "임상·실무약학2",    10),
    ("사회약학",     "임상·실무약학2",    20),
    # 4교시 법규
    ("약사법규",     "보건·의약관계법규", 10),
]


def upsert_subject(conn, name: str, session: int) -> int:
    cur = conn.execute("SELECT id FROM subjects WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO subjects (name, session) VALUES (?, ?)", (name, session))
    return cur.lastrowid


def upsert_chapter(conn, subject_id: int, parent_id: int | None,
                   chapter_no: int | None, name: str) -> int:
    if parent_id is None:
        cur = conn.execute(
            "SELECT id FROM chapters WHERE subject_id = ? AND name = ? AND parent_id IS NULL",
            (subject_id, name),
        )
    else:
        cur = conn.execute(
            "SELECT id FROM chapters WHERE subject_id = ? AND parent_id = ? AND name = ?",
            (subject_id, parent_id, name),
        )
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO chapters (subject_id, chapter_no, name, parent_id) VALUES (?, ?, ?, ?)",
        (subject_id, chapter_no, name, parent_id),
    )
    return cur.lastrowid


def reset_chapters(conn) -> None:
    """기존 chapters 비우고 questions.chapter_id도 NULL로."""
    conn.execute("UPDATE questions SET chapter_id = NULL")
    conn.execute("DELETE FROM chapters")


# ─────────────────────────────────────────────────────────────
# 약물치료학 단원 (질환별, 강의자료 파일명에서 추출)
# ─────────────────────────────────────────────────────────────
PREFIX_CATEGORY = {
    "NR": "신경계질환",
    "Psych": "정신과질환",
    "HO": "혈액·종양질환",
    "IMR": "호흡기질환",
    "IME": "내분비대사질환",
    "근골격계": "근골격계질환",
    "근골격계모듈": "근골격계질환",
    "소화기": "소화기질환",
}


def parse_lecture_filename(fn: str) -> dict | None:
    """{category, chapter_name, order} or None."""
    name = nfc(fn)
    base = name.rsplit(".", 1)[0]
    if "Introduction" in base or "강의개요" in base:
        return None

    m = re.match(r"^\[25약치\d?\]\s*(.+?)$", base)
    if m:
        rest = m.group(1)
        m2 = re.match(r"^([A-Za-z가-힣]+)\s*모듈[_\s]*(\d+)?[_\s]*(.+)$", rest)
        if m2:
            prefix, order, chapter = m2.group(1), m2.group(2), m2.group(3)
            cat = PREFIX_CATEGORY.get(prefix.strip(), prefix.strip())
            return {"category": cat, "chapter_name": chapter.strip(), "order": int(order) if order else 99}
        m3 = re.match(r"^([A-Za-z가-힣]+)[_\s]+(\d+)[_\s]+(.+)$", rest)
        if m3:
            prefix, order, chapter = m3.group(1), m3.group(2), m3.group(3)
            cat = PREFIX_CATEGORY.get(prefix.strip(), prefix.strip())
            return {"category": cat, "chapter_name": chapter.strip(), "order": int(order)}
        return {"category": "기타", "chapter_name": rest.strip(), "order": 99}

    m = re.match(r"^\[25약물치료학\]\s*([^_\s]+)\s*모듈[_\s]*(.+)$", base)
    if m:
        return {"category": m.group(1).strip(), "chapter_name": m.group(2).strip().lstrip("_"), "order": 50}

    m = re.match(r"^\[2025_HO\]\s*(\d+)[_\s]+(.+)$", base)
    if m:
        return {"category": PREFIX_CATEGORY["HO"], "chapter_name": m.group(2).strip(), "order": int(m.group(1))}

    m = re.match(r"^(NR|Psych)[_\s]+(.+)$", base)
    if m:
        prefix, chapter = m.group(1), m.group(2)
        chapter = chapter.strip().lstrip("_").rstrip(".")
        return {"category": PREFIX_CATEGORY[prefix], "chapter_name": chapter, "order": 99}

    m = re.match(r"^([\d,\-]+)[_\s]+([^_]+?)[_\s]+(.+?)(?:_(?:2025|2024|2023|2022|배포용).*)?$", base)
    if m:
        order_raw = m.group(1).split(",")[0].split("-")[0]
        try:
            order = int(order_raw)
        except ValueError:
            order = 99
        category = m.group(2).strip()
        chapter = m.group(3).strip()
        chapter = re.sub(r"_(2025|2024|2023|2022)(_배포용)?$", "", chapter)
        chapter = re.sub(r"_배포용$", "", chapter)
        return {"category": category, "chapter_name": chapter, "order": order}

    m = re.match(r"^([^_]+?)[_\s]+(.+?)(?:_(?:2025|2024|2023|2022|배포용).*)?$", base)
    if m:
        category = m.group(1).strip()
        chapter = m.group(2).strip()
        chapter = re.sub(r"_(2025|2024|2023|2022)(_배포용)?$", "", chapter)
        chapter = re.sub(r"_배포용$", "", chapter)
        return {"category": category, "chapter_name": chapter, "order": 99}

    return None


def import_chapters() -> dict:
    init_db()
    parsed_lectures = []
    skipped = []
    for f in sorted(LECTURE_DIR.iterdir()):
        if f.suffix.lower() != ".pdf":
            continue
        info = parse_lecture_filename(f.name)
        if info is None:
            skipped.append(f.name)
        else:
            parsed_lectures.append(info)

    with get_conn() as conn:
        # 1) 모든 chapters 클리어 + questions.chapter_id 초기화
        reset_chapters(conn)

        # 2) 가상 subject "약물치료학(가상)" (session=0) 보장
        upsert_subject(conn, "약물치료학(가상)", 0)

        # 3) 사용자 정의 15개 root chapters 시드
        chapter_id_by_name: dict[str, int] = {}
        for chapter_name, subject_name, order in USER_SUBJECTS_15:
            row = conn.execute("SELECT id FROM subjects WHERE name = ?", (subject_name,)).fetchone()
            if not row:
                continue
            sid = row["id"]
            cid = upsert_chapter(conn, sid, None, order, chapter_name)
            chapter_id_by_name[chapter_name] = cid

        # 4) 약물치료학 root chapter 아래에 카테고리 → 단원 트리
        drug_root_id = chapter_id_by_name["약물치료학"]
        drug_subject_id = conn.execute(
            "SELECT id FROM subjects WHERE name = ?", ("약물치료학(가상)",)
        ).fetchone()["id"]

        cat_counts: dict[str, int] = {}
        for info in parsed_lectures:
            # 카테고리 노드 (parent = 약물치료학 root)
            cat_id = upsert_chapter(conn, drug_subject_id, drug_root_id, None, info["category"])
            # 단원 노드 (parent = 카테고리)
            upsert_chapter(conn, drug_subject_id, cat_id, info["order"], info["chapter_name"])
            cat_counts[info["category"]] = cat_counts.get(info["category"], 0) + 1

    return {
        "user_subjects": len(USER_SUBJECTS_15),
        "drug_chapters": len(parsed_lectures),
        "by_category": cat_counts,
        "skipped": skipped,
    }


if __name__ == "__main__":
    result = import_chapters()
    print(f"세부 과목 시드: {result['user_subjects']}개")
    print(f"약물치료학 단원: {result['drug_chapters']}개")
    for cat, n in sorted(result["by_category"].items()):
        print(f"  · {cat}: {n}개")
    if result["skipped"]:
        print(f"\nSkipped ({len(result['skipped'])}): {result['skipped']}")
