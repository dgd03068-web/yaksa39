"""5년 출제 패턴 분석 + 빈출 약물·법규 리스트 → MD 리포트.

실행:
    python -m parsers.exam_patterns
출력:
    app/output/exam_patterns_YYYYMMDD.md
"""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import OUTPUT_DIR  # noqa: E402
from db import get_conn  # noqa: E402


YEARS = [2021, 2022, 2023, 2024, 2025]
CIRCLED = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤"}

# 약물명 추출용 패턴 — 한글 약물명 또는 한글(영문) 형태
DRUG_PAT = re.compile(r"([가-힣A-Za-z][가-힣A-Za-z\-]{1,20}?)\s*\(([A-Za-z][A-Za-z0-9\- ,/]{1,40})\)")
LAW_PAT = re.compile(r"(약사법|마약류관리법|국민건강증진법|보건의료기본법|국민건강보험법|지역보건법|의료법|식품위생법|화장품법|의료기기법)(?:\s*제(\d+)조)?")


def fetch_all_rows():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT q.id, e.year, s.name AS subject, q.question_number AS qnum,
                   q.body, q.answer, q.concept_tag, q.is_skipped,
                   c.name AS chapter, p.name AS chap_parent, c.id AS chap_id
            FROM questions q
            JOIN exams e ON q.exam_id = e.id
            JOIN subjects s ON q.subject_id = s.id
            LEFT JOIN chapters c ON q.chapter_id = c.id
            LEFT JOIN chapters p ON c.parent_id = p.id
            """
        ).fetchall()
        choices = conn.execute(
            "SELECT question_id, text FROM choices"
        ).fetchall()
    by_qid = defaultdict(list)
    for cr in choices:
        by_qid[cr["question_id"]].append(cr["text"])
    return [dict(r) for r in rows], by_qid


def analyze_chapter_frequency(rows):
    """sub-chapter별 5년 출제 빈도 (parent_id 있는 것만 = leaf 단원)."""
    chap_by_year: dict[tuple, Counter] = defaultdict(Counter)
    chap_names: dict = {}
    for r in rows:
        if r["is_skipped"]:
            continue
        if r["chap_id"] is None:
            continue
        key = (r["chap_parent"] or "", r["chapter"])
        chap_by_year[key][r["year"]] += 1
        chap_names[key] = r["subject"]
    out = []
    for key, by_year in chap_by_year.items():
        total = sum(by_year.values())
        out.append({
            "subject": chap_names[key],
            "parent": key[0],
            "name": key[1],
            "by_year": {y: by_year.get(y, 0) for y in YEARS},
            "total": total,
        })
    out.sort(key=lambda x: -x["total"])
    return out


def analyze_answer_distribution(rows):
    """정답 ①②③④⑤ 분포 (전체 + 과목별)."""
    overall = Counter()
    by_subject = defaultdict(Counter)
    for r in rows:
        if r["is_skipped"]:
            continue
        ans = r["answer"]
        if ans not in (1, 2, 3, 4, 5):
            continue
        overall[ans] += 1
        by_subject[r["subject"]][ans] += 1
    return overall, by_subject


def analyze_concept_keywords(rows):
    """concept_tag 빈출 키워드 — 전체 + 연도별."""
    overall = Counter()
    by_year = defaultdict(Counter)
    for r in rows:
        if r["is_skipped"]:
            continue
        tag = (r["concept_tag"] or "").strip()
        if not tag:
            continue
        # 키워드 단위로 분리 (· / , 등 구분자)
        parts = re.split(r"[·,/]+", tag)
        for p in parts:
            p = p.strip()
            if 2 <= len(p) <= 30:
                overall[p] += 1
                by_year[r["year"]][p] += 1
    return overall, by_year


def analyze_drugs(rows, choices_by_qid):
    """본문·보기·concept_tag에서 한글(영문) 약물명 추출 → 빈도 정렬."""
    drug_count = Counter()
    drug_years = defaultdict(set)
    for r in rows:
        if r["is_skipped"]:
            continue
        texts = [r["body"] or "", r["concept_tag"] or ""]
        texts.extend(choices_by_qid.get(r["id"], []))
        seen = set()
        for t in texts:
            for m in DRUG_PAT.finditer(t):
                kor, eng = m.group(1).strip(), m.group(2).strip()
                # 짧은 약물명 위주
                if 2 <= len(kor) <= 12 and 3 <= len(eng) <= 30:
                    key = f"{kor} ({eng})"
                    seen.add(key)
        for d in seen:
            drug_count[d] += 1
            drug_years[d].add(r["year"])
    out = []
    for drug, cnt in drug_count.most_common():
        out.append({"name": drug, "count": cnt, "years": sorted(drug_years[drug])})
    return out


def analyze_laws(rows, choices_by_qid):
    """법규 조항 빈출 — '약사법 제N조' 등."""
    law_count = Counter()
    for r in rows:
        if r["is_skipped"]:
            continue
        if "법규" not in (r["subject"] or ""):
            continue  # 법규 과목만
        texts = [r["body"] or "", r["concept_tag"] or ""]
        texts.extend(choices_by_qid.get(r["id"], []))
        seen = set()
        for t in texts:
            for m in LAW_PAT.finditer(t):
                law, article = m.group(1), m.group(2)
                key = f"{law} 제{article}조" if article else law
                seen.add(key)
        for k in seen:
            law_count[k] += 1
    return law_count.most_common()


# ──────────────────── 리포트 렌더링 ────────────────────
def _row(*cells):
    return "| " + " | ".join(str(c) if c is not None else "" for c in cells) + " |"


def render_report(rows, choices_by_qid):
    today = datetime.now().strftime("%Y-%m-%d")
    L = [f"# 약사국시 5년 출제 패턴 분석 ({today})\n"]

    valid = [r for r in rows if not r["is_skipped"]]
    L.append("## 0. 분석 대상\n")
    L.append(f"- 전체 문제: **{len(rows)}** / 학습 가능(is_skipped=0): **{len(valid)}**")
    L.append(f"- 분석 연도: {YEARS[0]}~{YEARS[-1]} (5년)\n")

    # 1. 단원별 출제 빈도
    chap = analyze_chapter_frequency(rows)
    L.append("## 1. Sub-chapter별 5년 출제 빈도 (상위 30)\n")
    L.append(_row("순위", "과목", "Parent", "Sub-chapter", *YEARS, "총합"))
    L.append(_row("---", "---", "---", "---", *(["---"] * 5), "---"))
    for i, c in enumerate(chap[:30], 1):
        L.append(_row(i, c["subject"], c["parent"], c["name"],
                      *(c["by_year"].get(y, 0) for y in YEARS), c["total"]))
    L.append("")

    # 2. 정답 분포
    overall, by_subject = analyze_answer_distribution(rows)
    total_ans = sum(overall.values())
    L.append("## 2. 정답 분포\n")
    L.append("### 2.1 전체\n")
    L.append(_row("정답", "건수", "비율"))
    L.append(_row("---", "---", "---"))
    for ans in (1, 2, 3, 4, 5):
        n = overall[ans]
        pct = n * 100 / total_ans if total_ans else 0
        L.append(_row(CIRCLED[ans], n, f"{pct:.1f}%"))
    L.append("")

    L.append("### 2.2 과목별\n")
    L.append(_row("과목", "총", *[CIRCLED[i] for i in range(1, 6)]))
    L.append(_row("---", "---", *(["---"] * 5)))
    for subj in sorted(by_subject):
        c = by_subject[subj]
        total = sum(c.values())
        cells = [f"{c[i]} ({c[i]*100/total:.0f}%)" if total else "0" for i in range(1, 6)]
        L.append(_row(subj, total, *cells))
    L.append("")

    # 3. 빈출 키워드 (concept_tag)
    kw_all, kw_by_year = analyze_concept_keywords(rows)
    L.append("## 3. 빈출 concept_tag 키워드 (상위 50)\n")
    L.append(_row("순위", "키워드", "출현 횟수"))
    L.append(_row("---", "---", "---"))
    for i, (kw, n) in enumerate(kw_all.most_common(50), 1):
        L.append(_row(i, kw, n))
    L.append("")

    # 4. 빈출 약물
    drugs = analyze_drugs(rows, choices_by_qid)
    L.append("## 4. 빈출 약물 (한글(영문) 매칭, 상위 80)\n")
    L.append(_row("순위", "약물", "출현 문제 수", "연도"))
    L.append(_row("---", "---", "---", "---"))
    for i, d in enumerate(drugs[:80], 1):
        L.append(_row(i, d["name"], d["count"], ", ".join(str(y) for y in d["years"])))
    L.append("")

    # 5. 빈출 법규조항
    laws = analyze_laws(rows, choices_by_qid)
    L.append("## 5. 빈출 법규조항 (보건·의약관계법규)\n")
    L.append(_row("순위", "법규", "출현 문제 수"))
    L.append(_row("---", "---", "---"))
    for i, (law, n) in enumerate(laws[:40], 1):
        L.append(_row(i, law, n))
    L.append("")

    return "\n".join(L)


def main():
    rows, choices_by_qid = fetch_all_rows()
    md = render_report(rows, choices_by_qid)
    out_path = OUTPUT_DIR / f"exam_patterns_{datetime.now().strftime('%Y%m%d')}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"✅ 패턴 분석 리포트: {out_path}")


if __name__ == "__main__":
    main()
