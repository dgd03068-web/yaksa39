"""빈출 약물 80개 PubChem 정보 + 약리 메타데이터 DB 적용."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path("/Users/seokjungpyo/Library/Mobile Documents/com~apple~CloudDocs/국시 학습 프로그램/app/data/questions.db")
TOP80 = Path("/tmp/top80_drugs.json")
PART_FILES = [Path("/tmp/drugs_part1.py"), Path("/tmp/drugs_part2.py")]


def main():
    top80 = json.loads(TOP80.read_text())
    ko_by_en = {d["name_en"].lower().strip(): d["name_ko"] for d in top80}
    cnt_by_en = {d["name_en"].lower().strip(): d["exam_count"] for d in top80}

    raw: list[dict] = []
    for f in PART_FILES:
        ns: dict = {}
        exec(f.read_text(encoding="utf-8"), ns)
        raw.extend(ns.get("DRUGS", []))

    # 정규화 — 두 sub-agent 키 형식이 다름
    normalized = []
    for d in raw:
        en = (d.get("name_en") or d.get("eng") or "").lower().strip()
        if not en:
            continue
        normalized.append({
            "name_en": en,
            "name_ko": ko_by_en.get(en) or d.get("kor") or d.get("name_ko") or "",
            "cid": d.get("cid"),
            "smiles": d.get("smiles") or "",
            "mw": d.get("mw"),
            "drug_class": d.get("drug_class") or "",
            "description": d.get("description") or "",
            "image_url": (
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{d['cid']}/PNG"
                if d.get("cid") else None
            ),
            "exam_count": cnt_by_en.get(en, 0),
        })

    print(f"정규화: {len(normalized)}개")
    print(f"name_ko 누락: {sum(1 for d in normalized if not d['name_ko'])}개")
    print(f"smiles 누락: {sum(1 for d in normalized if not d['smiles'])}개")
    print(f"cid 누락: {sum(1 for d in normalized if not d['cid'])}개")

    conn = sqlite3.connect(DB, timeout=30.0)
    n = 0
    for d in normalized:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO drugs "
                "(name_ko, name_en, cid, smiles, mw, drug_class, description, image_url, exam_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d["name_ko"], d["name_en"], d["cid"], d["smiles"],
                 d["mw"], d["drug_class"], d["description"], d["image_url"], d["exam_count"]),
            )
            n += 1
        except Exception as e:
            print(f"  ⚠️ {d['name_en']}: {e}")
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM drugs").fetchone()[0]
    classes = conn.execute(
        "SELECT drug_class, COUNT(*) FROM drugs GROUP BY drug_class ORDER BY COUNT(*) DESC LIMIT 10"
    ).fetchall()
    conn.close()
    print(f"\n✅ DB 적용: {n}개 (테이블 총 {total}개)")
    print("주요 약효군 상위 10:")
    for cls, cnt in classes:
        print(f"  {cls}: {cnt}개")


if __name__ == "__main__":
    main()
