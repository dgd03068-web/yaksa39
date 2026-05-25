"""notes/study_notes.md → {chapter_id: section_markdown} 매핑.

헤더 형식: `## [과목 · Parent] 단원명 (N문제)`
chapter name (+ parent name)로 chapters 테이블 lookup → chapter_id 매핑.
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import get_conn  # noqa: E402

NOTES_PATH = Path(__file__).resolve().parent.parent / "notes" / "study_notes.md"

HEADER_RE = re.compile(r"^##\s*\[([^·\]]+?)\s*·\s*([^\]]+?)\]\s*(.+?)\s*\((\d+)문제\)\s*$")


def _load_chapter_lookup() -> dict:
    """(parent_name, leaf_name) → chapter_id 매핑.
    fallback: (subject_name, leaf_name)."""
    lookup = {}
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name AS leaf, p.name AS parent, s.name AS subject
            FROM chapters c
            LEFT JOIN chapters p ON c.parent_id = p.id
            LEFT JOIN subjects s ON c.subject_id = s.id
        """).fetchall()
    for r in rows:
        leaf = (r["leaf"] or "").strip()
        parent = (r["parent"] or "").strip()
        subject = (r["subject"] or "").strip()
        if parent:
            lookup[(parent, leaf)] = r["id"]
        # fallback key
        lookup.setdefault((subject, leaf), r["id"])
    return lookup


@lru_cache(maxsize=1)
def parse_study_notes() -> dict:
    """{chapter_id: section_markdown_body} 반환. 모듈 로드 시 1회 캐싱."""
    if not NOTES_PATH.exists():
        return {}
    text = NOTES_PATH.read_text(encoding="utf-8")
    # `## ` 헤더로 split
    sections = re.split(r"^(?=## )", text, flags=re.MULTILINE)
    chapter_lookup = _load_chapter_lookup()
    out: dict = {}
    for sec in sections:
        sec = sec.strip()
        if not sec.startswith("## "):
            continue
        first_line, *rest = sec.split("\n", 1)
        m = HEADER_RE.match(first_line)
        if not m:
            continue
        subject, parent, leaf, _qcnt = m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4)
        # chapter_id lookup: parent 우선, fallback subject
        cid = chapter_lookup.get((parent, leaf)) or chapter_lookup.get((subject, leaf))
        if cid is None:
            continue
        body = rest[0].strip() if rest else ""
        # 다음 `---` 또는 끝까지가 한 단원 본문
        body = re.split(r"\n---\n", body, maxsplit=1)[0].strip()
        out[cid] = body
    return out


def get_chapter_note(chapter_id: int, max_chars: int = 400) -> str:
    """단원 노트 반환 (최대 길이 제한). 없으면 빈 문자열."""
    notes = parse_study_notes()
    raw = notes.get(chapter_id, "")
    if not raw:
        return ""
    # 첫 max_chars 자만, 빈 줄 정리
    snippet = raw[:max_chars]
    if len(raw) > max_chars:
        snippet = snippet.rstrip() + " …"
    return snippet


if __name__ == "__main__":
    notes = parse_study_notes()
    print(f"파싱된 단원 노트: {len(notes)}개")
    # 샘플
    for cid in list(notes.keys())[:3]:
        with get_conn() as conn:
            r = conn.execute("SELECT name FROM chapters WHERE id=?", (cid,)).fetchone()
        print(f"\n--- chapter_id={cid} ({r['name'] if r else '?'}) ---")
        print(notes[cid][:200])
