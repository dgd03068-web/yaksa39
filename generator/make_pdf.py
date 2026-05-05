"""Generate a printable worksheet PDF (questions + answer/explanation appendix).

WeasyPrint + Jinja2 기반 (2026-05-04 전환).
이전 ReportLab 버전은 ``make_pdf_reportlab.py.bak`` 보존.
"""
from __future__ import annotations

# ── macOS Homebrew 라이브러리 경로 (WeasyPrint import 전 필수, 로컬 macOS만) ──
# Linux(Streamlit Cloud)에서는 packages.txt로 시스템 라이브러리 설치되므로 불필요.
import os as _os
import sys as _sys
if _sys.platform == "darwin":
    _brew_lib = "/opt/homebrew/lib"
    if _os.path.isdir(_brew_lib) and _brew_lib not in _os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", ""):
        _os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
            _brew_lib + ":" + _os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        )

import datetime as dt
import json
import re
import sys
from html import escape as html_escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import OUTPUT_DIR  # noqa: E402
from db import get_conn  # noqa: E402

CHOICE_LABELS = ["①", "②", "③", "④", "⑤"]

TEMPLATES_DIR = Path(__file__).parent / "templates"
FONTS_DIR = Path(__file__).parent.parent / "fonts"


# ─────────────────────── 본문 줄바꿈·정리 ───────────────────────
SECTION_TAGS = (
    "병력", "활력징후", "임상검사", "복용약물", "복약이행도", "약물 알레르기",
    "약물알레르기", "사회력", "가족력", "처방내용", "처방", "진료과", "진단명",
    "경과기록", "혈액검사", "뇌척수액검사", "신속진단검사", "소변", "영상검사",
    "지질검사", "조직검사", "유전자검사", "임상소견", "검사결과", "복약지도",
    "지난 처방", "기존 처방", "오늘 처방", "추가 처방", "현재 처방",
    "약물요법", "사용약물", "검사", "기타", "비고", "참고", "투약내용",
    "복약 이행도", "약물 알러지", "환자정보", "환자력",
    "사용 약물", "병원약력", "지속기간",
    "사례", "새로 추가된 약물", "추가된 약물", "추가 약물", "새 처방",
    "새로운 처방", "신규 처방", "병용 약물", "투여 약물", "약력", "치료력",
    "수술력", "흡연력", "음주력", "예방접종력", "알레르기",
)

# [사례], [복용약물], [임상검사] 등 박스로 강조할 섹션
BOX_SECTIONS = (
    "사례", "복용약물", "사용약물", "사용 약물", "투약내용", "처방내용",
    "오늘 처방", "추가 처방", "현재 처방", "지난 처방", "기존 처방",
    "임상검사", "혈액검사", "검사결과", "활력징후", "임상소견",
    "약물요법", "병력", "약물 알레르기", "약물알레르기", "복약이행도",
    "복약 이행도", "사회력", "가족력", "예방접종력",
    "새로 추가된 약물", "추가된 약물", "추가 약물", "신규 처방",
    "병용 약물", "투여 약물", "처방",
)


def fix_spacing(s: str) -> str:
    """단위 결합 보정 — 숫자+영어단위 사이 공백 추가."""
    if not s:
        return ""
    s = re.sub(
        r'(\d)(mg|kg|mcg|μg|ng|mL|mmHg|mEq|IU|%|cm|mm|m\^2|m²|h|min|sec|ng/mL|mg/dL|mg/L)(?![A-Za-z])',
        r'\1 \2', s)
    s = re.sub(r' +', ' ', s)
    return s.strip()


def split_body_with_tables(body: str) -> list[tuple[str, object]]:
    """본문을 [표] 영역 기준으로 분리. 반환: list of ('text', str) | ('table', list[list[str]])"""
    if not body or '[표]' not in body:
        return [('text', body or '')]
    parts: list[tuple[str, object]] = []
    pattern = re.compile(r'\[표\][^\[]*?(?=\n\s*\n|$|\n\s*①|\n\s*\[)', re.DOTALL)
    pos = 0
    for m in pattern.finditer(body):
        if m.start() > pos:
            txt = body[pos:m.start()].strip()
            if txt:
                parts.append(('text', txt))
        tbl_str = m.group(0).replace('[표]', '', 1).strip()
        rows = [r.strip() for r in tbl_str.split('\n') if r.strip()]
        if rows:
            table = [[c.strip() for c in r.split('/')] for r in rows]
            n_cols = len(table[0])
            normalized = []
            for row in table:
                if len(row) == n_cols:
                    normalized.append(row)
                elif len(row) > n_cols:
                    normalized.append(row[:n_cols-1] + [' / '.join(row[n_cols-1:])])
                else:
                    normalized.append(row + [''] * (n_cols - len(row)))
            for i, row in enumerate(normalized):
                for j, cell in enumerate(row):
                    cell = re.sub(r'아침공복', '아침 공복', cell)
                    cell = re.sub(r'취침전', '취침 전', cell)
                    normalized[i][j] = cell
            parts.append(('table', normalized))
        pos = m.end()
    if pos < len(body):
        txt = body[pos:].strip()
        if txt:
            parts.append(('text', txt))
    if not parts:
        parts = [('text', body)]
    return parts


def text_to_html(text: str) -> str:
    """일반 텍스트 → HTML.

    - escape
    - [섹션태그] 패턴: BOX_SECTIONS이면 case-box로 감싸기
    - 묶음 사례 안내 문구 줄바꿈
    - [그림 비공개] 처리
    - (가)(나)(다) 자모 마커 줄바꿈
    """
    if not text:
        return ""
    s = fix_spacing(text)

    # 1) 섹션 분리: [태그] 패턴으로 split
    section_pat = re.compile(r'(\[(?:' + '|'.join(re.escape(t) for t in SECTION_TAGS) + r')\])')
    chunks = section_pat.split(s)
    # chunks: [pre_text, "[섹션1]", content1, "[섹션2]", content2, ...]

    box_set = set(BOX_SECTIONS)
    out_parts: list[str] = []
    i = 0
    # 첫 청크 = 섹션 태그 이전 텍스트
    if chunks and chunks[0].strip():
        out_parts.append(_format_normal(chunks[0].strip()))
    i = 1
    while i < len(chunks):
        tag = chunks[i]                                           # "[복용약물]"
        body_text = chunks[i + 1] if i + 1 < len(chunks) else ""  # 그 뒤 본문
        i += 2
        section_name = tag[1:-1].strip()
        formatted_body = _format_normal(body_text.strip())

        if section_name in box_set:
            # case-box로 감싸기
            inner = (
                f"<strong>{html_escape(tag)}</strong>"
                f"{(' ' + formatted_body) if formatted_body else ''}"
            )
            # 약물 리스트는 줄바꿈
            if section_name in ("복용약물", "사용약물", "사용 약물", "투약내용",
                                "처방내용", "오늘 처방", "추가 처방", "현재 처방",
                                "지난 처방", "기존 처방", "약물요법",
                                "새로 추가된 약물", "추가된 약물", "추가 약물",
                                "신규 처방", "병용 약물", "투여 약물"):
                inner = _split_drug_list_html(inner)
            out_parts.append(f'<div class="case-box">{inner}</div>')
        else:
            # 일반 섹션은 그냥 라벨 굵게 + 줄바꿈 표시
            out_parts.append(
                f'<br><strong>{html_escape(tag)}</strong>'
                f"{(' ' + formatted_body) if formatted_body else ''}"
            )

    return ''.join(out_parts).strip()


def _format_normal(text: str) -> str:
    """case-box 외 일반 텍스트 변환: escape + 묶음 사례 줄바꿈 + 자모 마커 줄바꿈."""
    if not text:
        return ""
    s = html_escape(text)
    # 묶음 사례: "다음 사례를 읽고 ... 고르시오."
    s = re.sub(r'(다음 사례를 읽고[^.]*\.)\s*', r'\1<br>', s)
    # (가)(나)(다) 자모 마커
    s = re.sub(r'\s*(\([가-하]\))\s+(?=[가-힣A-Za-z0-9])', r'<br>\1 ', s)
    # [그림 비공개]
    s = re.sub(r'\s*\[그림 비공개\]\s*',
               r'<span class="img-placeholder">[그림 비공개]</span>', s)
    return s.strip()


def _split_drug_list_html(html: str) -> str:
    """case-box 안의 약물 리스트를 줄바꿈."""
    drug_pat = (
        r'[가-힣]{2,}(?:\s*\([a-zA-Z][a-zA-Z\s\-]*\))?'
        r'\s*\d+(?:\.\d+)?\s*'
        r'(?:mg|g|mL|mcg|μg|ng|IU|%|단위|국제단위)'
    )
    return re.sub(r'\s+(?=' + drug_pat + r')', r'<br>', html)


def body_to_html(body: str, has_image: bool = False) -> str:
    """본문 전체 → HTML. [표] 영역과 텍스트 부분을 모두 처리."""
    if not body:
        return ""
    parts = split_body_with_tables(body)
    out: list[str] = []
    for kind, content in parts:
        if kind == 'text':
            out.append(text_to_html(content))
        elif kind == 'table':
            out.append(_table_to_html(content))
    return ''.join(out)


def _table_to_html(rows: list[list[str]]) -> str:
    """[표] 데이터 → HTML <table>."""
    if not rows:
        return ""
    out = ['<table>']
    for i, row in enumerate(rows):
        tag = 'th' if i == 0 else 'td'
        cells = ''.join(f'<{tag}>{html_escape(c)}</{tag}>' for c in row)
        out.append(f'<tr>{cells}</tr>')
    out.append('</table>')
    return ''.join(out)


def explanation_to_html(text: str) -> str:
    """해설 텍스트 → HTML.

    - escape
    - [핵심] 패턴 → core-tag 박스
    - ✅ 마커 → answer-check 색상
    """
    if not text:
        return ""
    s = html_escape(text)
    # [핵심] 강조
    s = re.sub(r'\[핵심\]', r'<span class="core-tag">[핵심]</span>', s)
    # ✅ 정답 마커
    s = s.replace('✅', '<span class="answer-check">✅</span>')
    return s


# ─────────────────────── DB 조회 (시그니처 유지) ───────────────────────
def fetch_questions(filters: dict) -> list[dict]:
    """기존 시그니처 유지 (UI 호환)."""
    where = []
    params: list = []
    yr = filters.get("year")
    if yr is None:
        pass
    elif isinstance(yr, (list, tuple, set)):
        if yr:
            where.append("e.year IN ({})".format(",".join("?" * len(yr))))
            params.extend(yr)
    else:
        where.append("e.year = ?")
        params.append(yr)
    if not where:
        where.append("1=1")
    if filters.get("subject_ids"):
        where.append("q.subject_id IN ({})".format(",".join("?" * len(filters["subject_ids"]))))
        params.extend(filters["subject_ids"])
    if not filters.get("include_hidden", True):
        where.append("q.has_image = 0")
    if filters.get("chapter_ids"):
        where.append("q.chapter_id IN ({})".format(",".join("?" * len(filters["chapter_ids"]))))
        params.extend(filters["chapter_ids"])
    if filters.get("unclassified_only"):
        where.append("q.chapter_id IS NULL")
    sql = f"""
      SELECT q.id, q.exam_id, e.year AS year, q.subject_id, s.name AS subject, s.session,
             q.chapter_id, c.name AS chapter_name, c.chapter_no,
             p.name AS category_name,
             q.question_number AS qnum, q.body, q.has_image,
             q.answer, q.explanation, q.concept_tag
      FROM questions q
      JOIN exams e ON q.exam_id = e.id
      JOIN subjects s ON q.subject_id = s.id
      LEFT JOIN chapters c ON q.chapter_id = c.id
      LEFT JOIN chapters p ON c.parent_id = p.id
      WHERE {' AND '.join(where)}
      ORDER BY
        CASE WHEN q.chapter_id IS NULL THEN 1 ELSE 0 END,
        p.name, c.chapter_no, c.id,
        e.year, q.question_number
    """
    out: list[dict] = []
    with get_conn() as conn:
        for row in conn.execute(sql, params):
            d = dict(row)
            ranges = filters.get("qnum_ranges") or {}
            r = ranges.get(d["subject_id"])
            if r and not (r[0] <= d["qnum"] <= r[1]):
                continue
            d["choices"] = [
                (c["number"], c["text"])
                for c in conn.execute(
                    "SELECT number, text FROM choices WHERE question_id = ? ORDER BY number",
                    (d["id"],),
                )
            ]
            out.append(d)
    return out


# ─────────────────────── 그룹화 + 컨텍스트 빌드 ───────────────────────
def _group_for_template(questions: list[dict]) -> tuple[list[dict], int]:
    """questions 리스트를 (subject → chapter → questions) 트리로 그룹화.

    각 question에 ``local_no`` (학습지 내 통합 순번) 부여.
    Returns (groups_list, total_count).
    """
    # 순번 부여 (전체 통합)
    for i, q in enumerate(questions, start=1):
        q["local_no"] = i

    groups: list[dict] = []
    cur_subject_id = object()
    cur_chapter_id = object()
    sub_buf: dict | None = None
    chap_buf: dict | None = None

    for q in questions:
        sid = q["subject_id"]
        cid = q.get("chapter_id")
        if sid != cur_subject_id:
            if sub_buf is not None:
                if chap_buf is not None:
                    sub_buf["chapters"].append(chap_buf)
                groups.append(sub_buf)
            sub_buf = {"subject_name": q["subject"], "chapters": []}
            cur_subject_id = sid
            chap_buf = None
            cur_chapter_id = object()

        if cid != cur_chapter_id:
            if chap_buf is not None:
                sub_buf["chapters"].append(chap_buf)
            if cid is None:
                label = "(단원 미분류)"
            elif q.get("category_name"):
                label = f"{q['category_name']} · {q['chapter_name']}"
            else:
                label = q.get("chapter_name") or "(미분류)"
            chap_buf = {
                "label": label,
                "show_header": True,  # 모든 단원 헤더 표시
                "questions": [],
            }
            cur_chapter_id = cid

        # 문제 → 템플릿용 dict 변환
        chap_buf["questions"].append({
            "local_no": q["local_no"],
            "id": q["id"],
            "year": q["year"],
            "session": q["session"],
            "qnum": q["qnum"],
            "body_html": body_to_html(q["body"] or "", q["has_image"]),
            "has_image": q["has_image"],
            "choices": [
                {"marker": CHOICE_LABELS[n - 1] if 1 <= n <= 5 else f"({n})",
                 "text": html_escape(text or "")}
                for n, text in q["choices"]
            ],
            "answer_marker": (CHOICE_LABELS[q["answer"] - 1]
                              if isinstance(q["answer"], int) and 1 <= q["answer"] <= 5
                              else "—"),
            "concept_tag": q["concept_tag"],
            "explanation_html": explanation_to_html(q["explanation"]) if q["explanation"] else "",
        })

    # 마지막 버퍼 flush
    if chap_buf is not None and sub_buf is not None:
        sub_buf["chapters"].append(chap_buf)
    if sub_buf is not None:
        groups.append(sub_buf)

    return groups, len(questions)


# ─────────────────────── PDF 렌더링 ───────────────────────
def render_pdf(questions: list[dict], filters: dict, out_path: Path) -> Path:
    """문제 영역 + 정답·해설 영역을 단일 PDF로 출력."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not questions:
        raise ValueError("렌더할 문제가 없습니다.")

    groups, total = _group_for_template(questions)

    # 표지 메타
    yr = filters.get("year")
    if yr is None or yr == "":
        title = "약사 국시 기출 학습지"
        subtitle = "5년치 통합"
    elif isinstance(yr, (list, tuple, set)):
        title = f"{','.join(map(str, sorted(yr)))}년 약사 국시 학습지"
        subtitle = ""
    else:
        title = f"{yr}년 약사 국시 학습지"
        subtitle = ""

    subj_names = sorted({q["subject"] for q in questions})
    subject_summary = " · ".join(subj_names)
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(['html']),
    )
    ws_html = env.get_template("worksheet.html").render(
        title=title, subtitle=subtitle, generated_at=generated_at,
        total_questions=total, subject_summary=subject_summary,
        groups=groups,
    )
    sol_html = env.get_template("solutions.html").render(
        groups=groups,
    )

    # 두 HTML을 합쳐 단일 PDF
    base_url = str(TEMPLATES_DIR) + "/"
    ws_doc = HTML(string=ws_html, base_url=base_url).render()
    sol_doc = HTML(string=sol_html, base_url=base_url).render()
    all_pages = list(ws_doc.pages) + list(sol_doc.pages)
    ws_doc.copy(all_pages).write_pdf(str(out_path))
    return out_path


def make_worksheet(filters: dict) -> Path:
    """기존 시그니처 유지."""
    qs = fetch_questions(filters)
    if not qs:
        raise ValueError("선택 조건에 해당하는 문제가 없습니다.")
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(OUTPUT_DIR) / f"worksheet_{ts}.pdf"
    render_pdf(qs, filters, out_path)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO worksheets (created_at, filter_json, pdf_path) VALUES (?, ?, ?)",
            (dt.datetime.now().isoformat(timespec="seconds"),
             json.dumps(filters, ensure_ascii=False), str(out_path)),
        )
    return out_path


if __name__ == "__main__":
    # 샘플: 1교시 약물치료학(고혈압) 16문제
    with get_conn() as conn:
        sb = conn.execute(
            "SELECT id FROM subjects WHERE name = ?", ("임상·실무약학1",)
        ).fetchone()
        if not sb:
            sb = conn.execute(
                "SELECT id FROM subjects WHERE name = ?", ("생명약학",)
            ).fetchone()
    filters = {
        "year": None,
        "subject_ids": [sb["id"]] if sb else [],
        "chapter_ids": [32],   # 고혈압
        "include_hidden": True,
    }
    p = make_worksheet(filters)
    print("생성:", p)
