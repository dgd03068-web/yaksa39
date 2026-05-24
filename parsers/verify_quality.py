"""DB 품질 검증 — PDF 정답 ↔ DB 비교, 해설 분석, 그림 의존 통계. MD 리포트만 생성.

실행:
    python -m parsers.verify_quality
    python -m parsers.verify_quality --force-ocr        # OCR 캐시 무시
    python -m parsers.verify_quality --years 2024 2025  # 특정 연도만
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import EXAM_FOLDERS, OUTPUT_DIR  # noqa: E402
from db import get_conn  # noqa: E402
from parsers.parse_answer import find_answer_pdf, parse_answer_pdf  # noqa: E402
from parsers._insert_2023_answers import (  # noqa: E402
    SESSION1 as A2023_S1,
    SESSION2 as A2023_S2,
    SESSION3 as A2023_S3,
    SESSION4_CLINICAL as A2023_S4C,
    SESSION4_LAW as A2023_S4L,
)

CACHE_FILE = Path("/tmp/answer_ocr_cache.json")

CIRCLED_MAP = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}
CIRCLED_RE = re.compile(r"정답\s*[:：]\s*([①②③④⑤])")

SUSPECT_PATTERNS = [
    re.compile(r"정답이?\s*[①②③④⑤\d]+\s*이?나"),
    re.compile(r"계산상\s*[①②③④⑤\d]"),
    re.compile(r"오류로?\s*보이"),
    re.compile(r"확정할?\s*수\s*없"),
    re.compile(r"논란"),
    re.compile(r"이의\s*제기"),
]


# ─────────────── 1) PDF OCR (캐시) ───────────────
def load_or_run_answer_ocr(years: list[int], force: bool = False) -> dict[int, list[dict]]:
    cache: dict[int, list[dict]] = {}
    if CACHE_FILE.exists() and not force:
        raw = json.loads(CACHE_FILE.read_text())
        cache = {int(k): v for k, v in raw.items()}

    result: dict[int, list[dict]] = {}
    for y in years:
        if y in cache:
            result[y] = cache[y]
            print(f"[캐시] {y}년 답안 {len(cache[y])}건 로드")
            continue
        try:
            pdf = find_answer_pdf(EXAM_FOLDERS[y])
            print(f"[OCR] {y} {pdf.name} 시작 …")
            t0 = time.time()
            rows = parse_answer_pdf(pdf)
            print(f"  → {len(rows)}건 ({time.time()-t0:.1f}s)")
            result[y] = rows
            cache[y] = rows
        except Exception as ex:
            print(f"  [실패] {y}: {ex}")
            result[y] = []

    CACHE_FILE.write_text(
        json.dumps({str(k): v for k, v in cache.items()}, ensure_ascii=False, indent=2)
    )
    return result


# ─────────────── 2) 2023 하드코딩 정답 ───────────────
def hardcoded_2023() -> list[dict]:
    out = []
    for i, a in enumerate(A2023_S1, start=1):
        out.append({"session": 1, "subject": "생명약학", "qnum": i, "answer": a})
    for i, a in enumerate(A2023_S2, start=1):
        out.append({"session": 2, "subject": "산업약학", "qnum": i, "answer": a})
    for i, a in enumerate(A2023_S3, start=1):
        out.append({"session": 3, "subject": "임상·실무약학1", "qnum": i, "answer": a})
    for i, a in enumerate(A2023_S4C, start=1):
        out.append({"session": 4, "subject": "임상·실무약학2", "qnum": i, "answer": a})
    for i, a in enumerate(A2023_S4L, start=64):
        out.append({"session": 4, "subject": "보건·의약관계법규", "qnum": i, "answer": a})
    return out


# ─────────────── 3) 점검 A: 정답 diff ───────────────
def check_answers(pdf_rows_by_year: dict[int, list[dict]]) -> list[dict]:
    mismatches: list[dict] = []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT q.id AS qid, e.year, s.name AS subject, s.session,
                   q.question_number AS qnum, q.answer
            FROM questions q
            JOIN exams e ON q.exam_id=e.id
            JOIN subjects s ON q.subject_id=s.id
            """
        ).fetchall()

    db_index: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["year"], r["subject"], r["qnum"])
        db_index.setdefault(key, []).append(
            {"qid": r["qid"], "answer": r["answer"], "session": r["session"]}
        )

    # 2023은 hardcoded로 통일
    pdf_rows_by_year[2023] = hardcoded_2023()

    pdf_index: dict[tuple, int] = {}
    for year, rs in pdf_rows_by_year.items():
        for r in rs:
            pdf_index[(year, r["subject"], r["qnum"])] = r["answer"]

    # DB에 있는데 PDF에 없는 키 = ghost / OCR 실패
    for key, entries in db_index.items():
        if key not in pdf_index:
            for e in entries:
                mismatches.append({
                    "qid": e["qid"], "year": key[0], "subject": key[1], "qnum": key[2],
                    "db_answer": e["answer"], "pdf_answer": None,
                    "source": "hardcoded(2023)" if key[0] == 2023 else "ocr",
                    "issue": "PDF/하드코딩에 매칭 없음",
                })

    # 정답 불일치
    for key, ans_pdf in pdf_index.items():
        if key not in db_index:
            continue
        for entry in db_index[key]:
            if entry["answer"] != ans_pdf:
                mismatches.append({
                    "qid": entry["qid"], "year": key[0], "subject": key[1], "qnum": key[2],
                    "db_answer": entry["answer"], "pdf_answer": ans_pdf,
                    "source": "hardcoded(2023)" if key[0] == 2023 else "ocr",
                    "issue": "정답 불일치",
                })
    return mismatches


# ─────────────── 4) 점검 B: 해설 품질 ───────────────
def check_explanations() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {k: [] for k in
                                  ("circled_mismatch", "no_circled", "too_short",
                                   "too_long", "no_keypoint", "suspect_lang")}
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT q.id AS qid, e.year, s.name AS subject, q.question_number AS qnum,
                   q.answer, q.explanation
            FROM questions q
            JOIN exams e ON q.exam_id=e.id
            JOIN subjects s ON q.subject_id=s.id
            WHERE q.explanation IS NOT NULL AND q.explanation != ''
            """
        ).fetchall()

    for r in rows:
        expl = r["explanation"]
        meta = {"qid": r["qid"], "year": r["year"], "subject": r["subject"],
                "qnum": r["qnum"], "answer": r["answer"]}

        m = CIRCLED_RE.search(expl)
        if not m:
            out["no_circled"].append(meta)
        else:
            n = CIRCLED_MAP[m.group(1)]
            if r["answer"] is not None and n != r["answer"]:
                out["circled_mismatch"].append({**meta, "expl_says": n,
                                                "snippet": expl[:120].replace("\n", " ")})

        L = len(expl)
        if L < 200:
            out["too_short"].append({**meta, "len": L})
        elif L > 1200:
            out["too_long"].append({**meta, "len": L})

        if "[핵심]" not in expl:
            out["no_keypoint"].append(meta)

        for p in SUSPECT_PATTERNS:
            m2 = p.search(expl)
            if m2:
                ctx = expl[max(0, m2.start()-30):m2.end()+50].replace("\n", " ")
                out["suspect_lang"].append({**meta, "matched": m2.group(0), "snippet": ctx})
                break
    return out


# ─────────────── 5) 점검 C: 그림 의존 ───────────────
def check_image_dependent() -> dict[str, list[dict]]:
    out = {"body_hidden": [], "choice_hidden": [], "all_choices_hidden": []}
    with get_conn() as conn:
        body_rows = conn.execute(
            """
            SELECT q.id AS qid, e.year, s.name AS subject, q.question_number AS qnum,
                   q.has_image
            FROM questions q
            JOIN exams e ON q.exam_id=e.id
            JOIN subjects s ON q.subject_id=s.id
            WHERE q.body LIKE '%[그림 비공개]%'
               OR q.body LIKE '%<자료(비공개)>%'
               OR q.body LIKE '%자료(비공개)%'
            """
        ).fetchall()
        for r in body_rows:
            out["body_hidden"].append({"qid": r["qid"], "year": r["year"], "subject": r["subject"],
                                       "qnum": r["qnum"], "has_image": r["has_image"]})

        choice_rows = conn.execute(
            """
            SELECT q.id AS qid, e.year, s.name AS subject, q.question_number AS qnum,
                   c.number
            FROM questions q
            JOIN choices c ON c.question_id=q.id
            JOIN exams e ON q.exam_id=e.id
            JOIN subjects s ON q.subject_id=s.id
            WHERE c.text LIKE '%[그림 비공개]%' OR c.text LIKE '%<자료(비공개)>%'
            """
        ).fetchall()

        by_q: dict[int, list] = defaultdict(list)
        meta_q: dict[int, dict] = {}
        for r in choice_rows:
            by_q[r["qid"]].append(r["number"])
            meta_q[r["qid"]] = {"qid": r["qid"], "year": r["year"],
                                "subject": r["subject"], "qnum": r["qnum"]}
        for qid, hidden in by_q.items():
            meta = meta_q[qid]
            out["choice_hidden"].append({**meta, "hidden_choices": sorted(hidden)})
            total = conn.execute("SELECT COUNT(*) c FROM choices WHERE question_id=?",
                                 (qid,)).fetchone()["c"]
            if total >= 5 and len(hidden) >= 5:
                out["all_choices_hidden"].append(meta)
    return out


# ─────────────── 6) 점검 D: 이상 행 ───────────────
def check_anomalies() -> dict[str, list[dict]]:
    out = {"qnum_zero": [], "null_answer": [], "duplicate_body": []}
    with get_conn() as conn:
        out["qnum_zero"] = [dict(r) for r in conn.execute(
            """
            SELECT q.id AS qid, e.year, s.name AS subject,
                   q.question_number AS qnum, q.answer
            FROM questions q JOIN exams e ON q.exam_id=e.id
            JOIN subjects s ON q.subject_id=s.id
            WHERE q.question_number <= 0
            """
        ).fetchall()]
        out["null_answer"] = [dict(r) for r in conn.execute(
            """
            SELECT q.id AS qid, e.year, s.name AS subject, q.question_number AS qnum
            FROM questions q JOIN exams e ON q.exam_id=e.id
            JOIN subjects s ON q.subject_id=s.id
            WHERE q.answer IS NULL OR q.answer < 1 OR q.answer > 5
            """
        ).fetchall()]
        out["duplicate_body"] = [dict(r) for r in conn.execute(
            """
            SELECT q1.id AS qid_a, q2.id AS qid_b, e.year, s.name AS subject,
                   q1.question_number AS qnum_a, q2.question_number AS qnum_b,
                   q1.answer AS ans_a, q2.answer AS ans_b,
                   SUBSTR(q1.body, 1, 80) AS body_snippet
            FROM questions q1
            JOIN questions q2 ON q1.exam_id=q2.exam_id AND q1.subject_id=q2.subject_id
                AND q1.id < q2.id
                AND LENGTH(q1.body) >= 20
                AND q1.body = q2.body
            JOIN exams e ON q1.exam_id=e.id
            JOIN subjects s ON q1.subject_id=s.id
            """
        ).fetchall()]
    return out


# ─────────────── 7) audit 요약 ───────────────
def check_audit_summary() -> dict:
    with get_conn() as conn:
        try:
            by_source = [dict(r) for r in conn.execute(
                "SELECT source, field, COUNT(*) c FROM audit GROUP BY source, field ORDER BY c DESC"
            ).fetchall()]
            recent = [dict(r) for r in conn.execute(
                "SELECT resolved_at, question_id, field, source FROM audit ORDER BY id DESC LIMIT 20"
            ).fetchall()]
        except Exception:
            return {"available": False}
    return {"available": True, "by_source": by_source, "recent": recent}


# ─────────────── 8) 권장 후속 조치 ───────────────
def build_recommendations(answer_diff, expl, image_dep, anomalies) -> list[str]:
    recs = []
    ghost_a = {a["qid_a"] for a in anomalies["duplicate_body"]}
    ghost_b = {a["qid_b"] for a in anomalies["duplicate_body"]}
    qnum0_ids = {q["qid"] for q in anomalies["qnum_zero"]}
    ghost = (ghost_a | ghost_b) & qnum0_ids
    for qid in sorted(ghost):
        recs.append(f"- **qid={qid}** (qnum=0 + 중복 본문): ghost row 의심. 정답 PDF 대조 후 DELETE 또는 question_number 보정 검토.")

    hc_diffs = [d for d in answer_diff if d.get("source") == "hardcoded(2023)" and d.get("issue") == "정답 불일치"]
    if hc_diffs:
        recs.append(f"- 2023년 hardcoded ↔ DB 불일치 **{len(hc_diffs)}건**: `_insert_2023_answers.py`가 진실값. DB 보정 필요.")

    ocr_diffs = [d for d in answer_diff if d.get("source") == "ocr" and d.get("issue") == "정답 불일치"]
    if ocr_diffs:
        recs.append(f"- OCR ↔ DB 불일치 **{len(ocr_diffs)}건**: 원본 PDF로 사람이 직접 확인 후 보정.")

    no_match = [d for d in answer_diff if d.get("issue") == "PDF/하드코딩에 매칭 없음"]
    if no_match:
        recs.append(f"- PDF/하드코딩에 매칭 없는 DB 행 **{len(no_match)}건**: ghost row 또는 OCR 누락 가능성. 우선 점검.")

    if expl["circled_mismatch"]:
        recs.append(f"- 해설 동그라미정답 ↔ DB answer 불일치 **{len(expl['circled_mismatch'])}건**: 어느 쪽이 옳은지 사람 검토.")

    if image_dep["all_choices_hidden"]:
        recs.append(f"- 보기 5개 전부 [그림 비공개] **{len(image_dep['all_choices_hidden'])}건**: 본문 단서만으로 정답 검증 불가. 원본 PDF로 그림 보강하거나 비활성화 검토.")

    if expl["no_circled"]:
        recs.append(f"- 해설에 `정답: ⓪` 패턴 없는 **{len(expl['no_circled'])}건**: 형식 보정 권장.")

    if expl["no_keypoint"]:
        recs.append(f"- `[핵심]` 누락 **{len(expl['no_keypoint'])}건**: 형식 보정 권장.")

    return recs


# ─────────────── 9) 리포트 렌더링 ───────────────
def _row(*cells) -> str:
    return "| " + " | ".join(str(c) if c is not None else "" for c in cells) + " |"


def render_markdown(answer_diff, expl, image_dep, anomalies, audit, totals, recs) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    L = []
    L.append(f"# 약사국시 DB 품질 검증 리포트 ({today})\n")

    L.append("## 1. 요약 통계\n")
    n_mismatch = sum(1 for x in answer_diff if x.get("issue") == "정답 불일치")
    n_nomatch = sum(1 for x in answer_diff if x.get("issue") == "PDF/하드코딩에 매칭 없음")
    L.append(f"- 전체 문제: **{totals['n_questions']}**, 해설: **{totals['n_with_expl']}**, 정답: **{totals['n_with_ans']}**")
    L.append(f"- 정답 불일치 (PDF↔DB): **{n_mismatch}건**")
    L.append(f"- PDF/하드코딩에 매칭 없음(ghost 의심): **{n_nomatch}건**")
    L.append(f"- 해설 정답표기 ↔ DB 불일치: **{len(expl['circled_mismatch'])}건**")
    L.append(f"- 해설 `정답: ⓪` 패턴 누락: **{len(expl['no_circled'])}건**")
    L.append(f"- 해설 `[핵심]` 누락: **{len(expl['no_keypoint'])}건**")
    L.append(f"- 해설 길이 < 200자: **{len(expl['too_short'])}건**, > 1200자: **{len(expl['too_long'])}건**")
    L.append(f"- 해설 의심 표현 매칭: **{len(expl['suspect_lang'])}건**")
    L.append(f"- 그림 비공개 본문: **{len(image_dep['body_hidden'])}건**")
    L.append(f"- 보기 비공개 포함: **{len(image_dep['choice_hidden'])}건**, 5개 보기 전부 비공개: **{len(image_dep['all_choices_hidden'])}건**")
    L.append(f"- qnum ≤ 0 행: **{len(anomalies['qnum_zero'])}건**, NULL/범위밖 answer: **{len(anomalies['null_answer'])}건**, 중복 본문 쌍: **{len(anomalies['duplicate_body'])}건**\n")

    # 2) 정답 diff
    L.append("## 2. 정답 불일치 (PDF/하드코딩 ↔ DB)\n")
    if answer_diff:
        L.append(_row("qid", "year", "subject", "qnum", "DB", "PDF", "source", "issue"))
        L.append(_row("---", "---", "---", "---", "---", "---", "---", "---"))
        for x in sorted(answer_diff, key=lambda r: (r["year"], r["subject"], r["qnum"], r["qid"])):
            L.append(_row(x["qid"], x["year"], x["subject"], x["qnum"],
                          x.get("db_answer"), x.get("pdf_answer"), x["source"], x["issue"]))
    else:
        L.append("_없음_")
    L.append("")

    # 3) 해설 의심
    L.append("## 3. 해설 의심\n")
    L.append("### 3.1 해설 정답표기 ↔ DB answer 불일치\n")
    if expl["circled_mismatch"]:
        L.append(_row("qid", "year", "subject", "qnum", "DB", "expl", "snippet"))
        L.append(_row("---", "---", "---", "---", "---", "---", "---"))
        for x in expl["circled_mismatch"]:
            L.append(_row(x["qid"], x["year"], x["subject"], x["qnum"],
                          x["answer"], x["expl_says"], x["snippet"][:80]))
    else:
        L.append("_없음_")
    L.append("")

    L.append("### 3.2 의심 표현 매칭\n")
    if expl["suspect_lang"]:
        L.append(_row("qid", "year", "subject", "qnum", "matched", "snippet"))
        L.append(_row("---", "---", "---", "---", "---", "---"))
        for x in expl["suspect_lang"][:50]:
            L.append(_row(x["qid"], x["year"], x["subject"], x["qnum"], x["matched"], x["snippet"][:80]))
        if len(expl["suspect_lang"]) > 50:
            L.append(f"_…외 {len(expl['suspect_lang'])-50}건 생략_")
    else:
        L.append("_없음_")
    L.append("")

    L.append("### 3.3 `정답: ⓪` 패턴 없는 해설 (상위 20)\n")
    if expl["no_circled"]:
        L.append(_row("qid", "year", "subject", "qnum", "answer"))
        L.append(_row("---", "---", "---", "---", "---"))
        for x in expl["no_circled"][:20]:
            L.append(_row(x["qid"], x["year"], x["subject"], x["qnum"], x["answer"]))
        if len(expl["no_circled"]) > 20:
            L.append(f"_…외 {len(expl['no_circled'])-20}건 생략 (총 {len(expl['no_circled'])}건)_")
    else:
        L.append("_없음_")
    L.append("")

    L.append("### 3.4 `[핵심]` 누락 (상위 20)\n")
    if expl["no_keypoint"]:
        L.append(_row("qid", "year", "subject", "qnum"))
        L.append(_row("---", "---", "---", "---"))
        for x in expl["no_keypoint"][:20]:
            L.append(_row(x["qid"], x["year"], x["subject"], x["qnum"]))
        if len(expl["no_keypoint"]) > 20:
            L.append(f"_…외 {len(expl['no_keypoint'])-20}건 생략 (총 {len(expl['no_keypoint'])}건)_")
    else:
        L.append("_없음_")
    L.append("")

    L.append("### 3.5 해설 길이 < 200자 (상위 20)\n")
    if expl["too_short"]:
        L.append(_row("qid", "year", "subject", "qnum", "len"))
        L.append(_row("---", "---", "---", "---", "---"))
        for x in sorted(expl["too_short"], key=lambda r: r["len"])[:20]:
            L.append(_row(x["qid"], x["year"], x["subject"], x["qnum"], x["len"]))
        if len(expl["too_short"]) > 20:
            L.append(f"_…외 {len(expl['too_short'])-20}건 생략_")
    else:
        L.append("_없음_")
    L.append("")

    # 4) 그림 의존
    L.append("## 4. 그림 의존 문제\n")
    L.append("### 4.1 보기 5개 전부 [그림 비공개]\n")
    if image_dep["all_choices_hidden"]:
        L.append(_row("qid", "year", "subject", "qnum"))
        L.append(_row("---", "---", "---", "---"))
        for x in image_dep["all_choices_hidden"]:
            L.append(_row(x["qid"], x["year"], x["subject"], x["qnum"]))
    else:
        L.append("_없음_")
    L.append("")

    L.append(f"### 4.2 본문 그림 의존 ({len(image_dep['body_hidden'])}건) — 과목별 통계\n")
    if image_dep["body_hidden"]:
        c = Counter(x["subject"] for x in image_dep["body_hidden"])
        L.append(_row("subject", "count"))
        L.append(_row("---", "---"))
        for s, n in c.most_common():
            L.append(_row(s, n))
    L.append("")

    # 5) 이상 행
    L.append("## 5. 이상 행\n")
    L.append("### 5.1 question_number ≤ 0\n")
    if anomalies["qnum_zero"]:
        L.append(_row("qid", "year", "subject", "qnum", "answer"))
        L.append(_row("---", "---", "---", "---", "---"))
        for x in anomalies["qnum_zero"]:
            L.append(_row(x["qid"], x["year"], x["subject"], x["qnum"], x["answer"]))
    else:
        L.append("_없음_")
    L.append("")

    L.append("### 5.2 정답 NULL 또는 1~5 범위 밖\n")
    if anomalies["null_answer"]:
        L.append(_row("qid", "year", "subject", "qnum"))
        L.append(_row("---", "---", "---", "---"))
        for x in anomalies["null_answer"]:
            L.append(_row(x["qid"], x["year"], x["subject"], x["qnum"]))
    else:
        L.append("_없음_")
    L.append("")

    L.append("### 5.3 본문 중복 (같은 시험·과목 내, 본문 ≥ 20자)\n")
    if anomalies["duplicate_body"]:
        L.append(_row("qid_a", "qid_b", "year", "subject", "qnum_a", "qnum_b", "ans_a", "ans_b", "snippet"))
        L.append(_row("---", "---", "---", "---", "---", "---", "---", "---", "---"))
        for x in anomalies["duplicate_body"]:
            snip = (x.get("body_snippet") or "").replace("\n", " ")[:60]
            L.append(_row(x["qid_a"], x["qid_b"], x["year"], x["subject"],
                          x["qnum_a"], x["qnum_b"], x["ans_a"], x["ans_b"], snip))
    else:
        L.append("_없음_")
    L.append("")

    # 6) audit
    L.append("## 6. audit 이력 요약\n")
    if audit.get("available"):
        L.append("### 6.1 source · field 별 변경 카운트\n")
        L.append(_row("source", "field", "count"))
        L.append(_row("---", "---", "---"))
        for x in audit["by_source"]:
            L.append(_row(x["source"], x["field"], x["c"]))
        L.append("")
        L.append("### 6.2 최근 변경 20건\n")
        L.append(_row("resolved_at", "qid", "field", "source"))
        L.append(_row("---", "---", "---", "---"))
        for x in audit["recent"]:
            L.append(_row(x["resolved_at"], x["question_id"], x["field"], x["source"]))
    else:
        L.append("_audit 테이블 사용 불가_")
    L.append("")

    # 7) 권장
    L.append("## 7. 권장 후속 조치\n")
    if recs:
        L.extend(recs)
    else:
        L.append("_해당 없음_")
    L.append("")

    return "\n".join(L)


# ─────────────── 10) main ───────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-ocr", action="store_true")
    ap.add_argument("--years", nargs="+", type=int, default=[2021, 2022, 2023, 2024, 2025])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    # 2023은 hardcoded로 처리하므로 OCR 대상에서 제외
    ocr_years = [y for y in args.years if y != 2023 and y in EXAM_FOLDERS]
    pdf_rows = load_or_run_answer_ocr(ocr_years, force=args.force_ocr)

    print("\n[A] 정답 diff …")
    answer_diff = check_answers(pdf_rows)
    print(f"  → {len(answer_diff)}건")
    print("[B] 해설 품질 …")
    expl = check_explanations()
    print("[C] 그림 의존 …")
    image_dep = check_image_dependent()
    print("[D] 이상 행 …")
    anomalies = check_anomalies()
    print("[E] audit 요약 …")
    audit = check_audit_summary()

    with get_conn() as conn:
        totals = dict(conn.execute(
            """
            SELECT COUNT(*) AS n_questions,
                   SUM(CASE WHEN explanation IS NOT NULL AND explanation!='' THEN 1 ELSE 0 END) AS n_with_expl,
                   SUM(CASE WHEN answer BETWEEN 1 AND 5 THEN 1 ELSE 0 END) AS n_with_ans
            FROM questions
            """
        ).fetchone())

    recs = build_recommendations(answer_diff, expl, image_dep, anomalies)
    md = render_markdown(answer_diff, expl, image_dep, anomalies, audit, totals, recs)

    out_path = args.out or (OUTPUT_DIR / f"quality_report_{datetime.now().strftime('%Y%m%d')}.md")
    out_path.write_text(md, encoding="utf-8")
    print(f"\n✅ 리포트: {out_path}")


if __name__ == "__main__":
    main()
