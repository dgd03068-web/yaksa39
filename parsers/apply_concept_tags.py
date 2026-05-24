"""concept_tag 일괄 적용.

sub-agent 5개가 작성한 `/tmp/concept_inline/{year}_tags.py`(TAGS 리스트)를 읽어
`questions.concept_tag` 컬럼에 UPDATE.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import Counter
from pathlib import Path

DB = Path("/Users/seokjungpyo/Library/Mobile Documents/com~apple~CloudDocs/국시 학습 프로그램/app/data/questions.db")
SRC_DIR = Path("/tmp/concept_inline")
YEARS = [2021, 2022, 2023, 2024, 2025]


def main():
    ts = dt.datetime.now().isoformat(timespec="seconds")
    all_tags: list[tuple[int, str]] = []
    for year in YEARS:
        path = SRC_DIR / f"{year}_tags.py"
        if not path.exists():
            print(f"⚠️ {year}: {path} 없음 — 건너뜀")
            continue
        ns: dict = {}
        exec(path.read_text(encoding="utf-8"), ns)
        data = ns.get("TAGS") or ns.get("tags")
        if not data:
            print(f"⚠️ {year}: TAGS 변수 없음")
            continue
        print(f"  {year}: {len(data)}건 추출")
        all_tags.extend(data)

    if not all_tags:
        print("❌ 데이터 없음 — 종료")
        return

    # 중복 qid 검사
    qid_counts = Counter(t[0] for t in all_tags)
    dups = [q for q, n in qid_counts.items() if n > 1]
    if dups:
        print(f"⚠️ 중복 qid {len(dups)}개 (마지막 값으로 덮어씀): {dups[:10]}")

    print(f"\n총 {len(all_tags)}건 적용 중 …")
    conn = sqlite3.connect(DB, timeout=30.0)
    n_upd = 0
    n_skip = 0
    for qid, tag in all_tags:
        if not tag or not isinstance(tag, str):
            n_skip += 1
            continue
        tag = tag.strip()
        if len(tag) > 60:
            tag = tag[:60]
        row = conn.execute("SELECT concept_tag FROM questions WHERE id=?", (qid,)).fetchone()
        if not row:
            print(f"  ⚠️ qid={qid} DB에 없음 — 건너뜀")
            n_skip += 1
            continue
        old = row[0] or ""
        conn.execute("UPDATE questions SET concept_tag=? WHERE id=?", (tag, qid))
        conn.execute(
            "INSERT INTO audit (question_id, field, old_value, new_value, source, confidence, resolved_at) "
            "VALUES (?, 'concept_tag', ?, ?, 'ai-vision-agent', 0.85, ?)",
            (qid, old, tag, ts),
        )
        n_upd += 1
    conn.commit()

    # 검증
    total = conn.execute("SELECT COUNT(*) FROM questions WHERE is_skipped=0").fetchone()[0]
    filled = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE is_skipped=0 AND concept_tag IS NOT NULL AND concept_tag != ''"
    ).fetchone()[0]
    conn.close()

    print(f"\n✅ 업데이트: {n_upd}건 (건너뜀: {n_skip})")
    print(f"   is_skipped=0 중 concept_tag 채워진 비율: {filled}/{total} ({filled*100/total:.1f}%)")


if __name__ == "__main__":
    main()
