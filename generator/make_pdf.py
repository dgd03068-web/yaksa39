"""Generate a printable worksheet PDF (questions + answer/explanation appendix)."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import re

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    PageBreak,
    KeepTogether,
    Table,
    TableStyle,
)


# ─────────────────────── 본문 줄바꿈·정리 (사용자 요청) ───────────────────────
# 임상 사례 문제에서 자주 나오는 섹션 태그들 — 각 태그 앞에서 줄바꿈
SECTION_TAGS = (
    "병력", "활력징후", "임상검사", "복용약물", "복약이행도", "약물 알레르기",
    "약물알레르기", "사회력", "가족력", "처방내용", "처방", "진료과", "진단명",
    "경과기록", "혈액검사", "뇌척수액검사", "신속진단검사", "소변", "영상검사",
    "지질검사", "조직검사", "유전자검사", "임상소견", "검사결과", "복약지도",
    "지난 처방", "기존 처방", "오늘 처방", "추가 처방", "현재 처방",
    "약물요법", "사용약물", "검사", "기타", "비고", "참고", "투약내용",
    "복약 이행도", "복약이행도", "약물 알러지", "환자정보", "환자력",
    "사용 약물", "병원약력", "지속기간",
    # 묶음/사례 문제 추가 섹션
    "사례", "새로 추가된 약물", "추가된 약물", "추가 약물", "새 처방",
    "새로운 처방", "신규 처방", "병용 약물", "투여 약물", "약력", "치료력",
    "수술력", "흡연력", "음주력", "예방접종력", "알레르기",
)


def add_linebreaks(s: str) -> str:
    """본문 안에 [섹션태그] 패턴 발견 시 그 앞에 줄바꿈(<br/>) 삽입."""
    if not s:
        return ""
    # 1) 섹션 태그 앞에 줄바꿈 (단, 본문 시작 부분 첫 태그는 줄바꿈 안 함)
    pattern = r'\s*(\[(?:' + '|'.join(re.escape(t) for t in SECTION_TAGS) + r')\])'
    s = re.sub(pattern, r'<br/>\1', s)
    # 2) 묶음 문제 사례 끝 패턴: "다음 사례를 읽고 각 문제에 적합한 답을 고르시오." 다음 줄바꿈
    s = re.sub(r'(다음 사례를 읽고[^.]*\.)\s*', r'\1<br/>', s)
    # 3) [그림 비공개] 앞·뒤 줄바꿈 (사용자가 그림 자리 알기 쉽게)
    s = re.sub(r'\s*(\[그림 비공개\])\s*', r'<br/>\1<br/>', s)
    # 4) 한글 자모 리스트 마커 앞에 줄바꿈: (가) (나) (다) (라) ...
    #    - "(가) 침전속도는..." → 앞에 줄바꿈 (마커 뒤 공백 + 한글/영문 시작)
    #    - "(다)와 (라)에서" → NO match (마커 뒤 한글 조사 바로 붙음)
    #    매칭 조건: 양쪽이 공백/문장끝, 마커 뒤에 공백 + 내용
    s = re.sub(r'\s*(\([가-하]\))\s+(?=[가-힣A-Za-z0-9])', r'<br/>\1 ', s)
    # 5) [복용약물] / [처방내용] / [사용약물] 섹션 안의 약물 리스트 줄바꿈
    #    약물 패턴: 한글이름(영문이름)? 용량(숫자+mg/g 등)
    #    예: "아스피린(aspirin) 100 mg", "로사르탄(losartan) 50mg", "디곡신 0.125 mg"
    drug_pat = (
        r'[가-힣]{2,}(?:\s*\([a-zA-Z][a-zA-Z\s\-]*\))?'   # 한글이름 + (영문)?
        r'\s*\d+(?:\.\d+)?\s*'                             # 용량 숫자
        r'(?:mg|g|mL|mcg|μg|ng|IU|%|단위|국제단위)'        # 단위
    )
    drug_section_tags = ('복용약물', '처방내용', '사용약물', '투약내용', '오늘 처방',
                         '추가 처방', '현재 처방', '지난 처방', '기존 처방', '약물요법',
                         '사용 약물', '새로 추가된 약물', '추가된 약물', '추가 약물',
                         '새 처방', '새로운 처방', '신규 처방', '병용 약물', '투여 약물')
    drug_section_alt = '|'.join(re.escape(t) for t in drug_section_tags)
    def split_drug_list(m):
        tag = m.group(1)
        content = m.group(2)
        # 약물 패턴 앞에 줄바꿈 삽입
        new_content = re.sub(r'\s+(?=' + drug_pat + r')', r'<br/>', content)
        return tag + new_content
    # [복용약물] 부터 다음 [...] (다른 섹션 태그) 또는 끝까지
    s = re.sub(r'(\[(?:' + drug_section_alt + r')\])([^\[]*)', split_drug_list, s)
    # 6) 본문 시작 부분에 <br/> 있으면 제거
    s = re.sub(r'^(\s*<br/>)+', '', s)
    # 7) 연속된 <br/> 정리
    s = re.sub(r'(<br/>\s*){2,}', '<br/>', s)
    # 8) 다중 공백 정리
    s = re.sub(r' +', ' ', s)
    return s.strip()


def split_body_with_tables(body: str) -> list[tuple[str, object]]:
    """본문을 [표] 영역 기준으로 분리. table_restore_instruction.md 규칙.

    반환: list of ('text', str) | ('table', list[list[str]])
    """
    if not body or '[표]' not in body:
        return [('text', body or '')]
    parts: list[tuple[str, object]] = []
    # [표] ~ \n\n (or end) 패턴으로 영역 추출
    pattern = re.compile(r'\[표\][^\[]*?(?=\n\s*\n|$|\n\s*①|\n\s*\[)', re.DOTALL)
    pos = 0
    for m in pattern.finditer(body):
        # 표 이전 텍스트
        if m.start() > pos:
            txt = body[pos:m.start()].strip()
            if txt:
                parts.append(('text', txt))
        # 표 영역
        tbl_str = m.group(0).replace('[표]', '', 1).strip()
        rows = [r.strip() for r in tbl_str.split('\n') if r.strip()]
        if rows:
            table = [[c.strip() for c in r.split('/')] for r in rows]
            # 셀 수 맞추기 (헤더 기준으로 정규화)
            n_cols = len(table[0])
            normalized = []
            for row in table:
                if len(row) == n_cols:
                    normalized.append(row)
                elif len(row) > n_cols:
                    # 마지막 셀에 나머지 합치기
                    normalized.append(row[:n_cols-1] + [' / '.join(row[n_cols-1:])])
                else:
                    # 부족하면 빈 셀 추가
                    normalized.append(row + [''] * (n_cols - len(row)))
            # 가독성 보정 (table_restore_instruction.md 7번): "아침공복" → "아침 공복"
            for i, row in enumerate(normalized):
                for j, cell in enumerate(row):
                    cell = re.sub(r'아침공복', '아침 공복', cell)
                    cell = re.sub(r'취침전', '취침 전', cell)
                    cell = re.sub(r'식전', '식 전', cell) if cell == '식전' else cell
                    normalized[i][j] = cell
            parts.append(('table', normalized))
        pos = m.end()
    # 표 이후 잔여 텍스트
    if pos < len(body):
        txt = body[pos:].strip()
        if txt:
            parts.append(('text', txt))
    if not parts:
        parts = [('text', body)]
    return parts


def fix_spacing(s: str) -> str:
    """단위 결합 보정 — 숫자+영어단위(mg, kg, mL 등) 사이 공백 추가.
    한글 단위(일, 회, 세 등)는 붙임. 너무 공격적이지 않게.
    """
    if not s:
        return ""
    # 숫자 다음 영어 단위(mg, kg, mL, mmHg, %, m, L) 사이 공백 (예: "100mg" → "100 mg")
    s = re.sub(r'(\d)(mg|kg|mcg|μg|ng|mL|mmHg|mEq|IU|%|cm|mm|m\^2|m²|h|min|sec|ng/mL|mg/dL|mg/L)(?![A-Za-z])',
               r'\1 \2', s)
    # 다중 공백 → 단일
    s = re.sub(r' +', ' ', s)
    return s.strip()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import OUTPUT_DIR  # noqa: E402
from db import get_conn  # noqa: E402

# 한글 폰트 — macOS 기본 AppleGothic.ttf (TTC가 아니라 ReportLab 호환)
KOREAN_FONT_PATH = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")
KOREAN_FONT_NAME = "KRBody"
KOREAN_BOLD_NAME = "KRBold"

CHOICE_LABELS = ["①", "②", "③", "④", "⑤"]


def register_fonts() -> None:
    if KOREAN_FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(KOREAN_FONT_NAME, str(KOREAN_FONT_PATH)))
    # AppleGothic은 단일 weight라 Bold도 동일 폰트 (시각적 강조는 ParagraphStyle에서)
    pdfmetrics.registerFont(TTFont(KOREAN_BOLD_NAME, str(KOREAN_FONT_PATH)))


def build_styles() -> dict[str, ParagraphStyle]:
    register_fonts()
    base = getSampleStyleSheet()["Normal"]
    # exam_style_guide.md 적용 (2026-05-02 갱신):
    #   - 본문 10pt / 보기 10pt / 시험 제목 18pt / 섹션 헤더 14pt / 페이지번호 9pt
    #   - 자간 -0.01em ≈ ReportLab charSpace=-0.1
    #   - 행간 1.6 (leading = fontSize × 1.6)
    #   - 색상 흑백 단색 (worksheet) — concept_tag 빨간색은 Good note 견본 양식 우선 (solutions만)
    BS = -0.1  # body charSpace (자간)
    return {
        "title": ParagraphStyle(
            "Title", parent=base, fontName=KOREAN_BOLD_NAME,
            fontSize=18, leading=22, alignment=1, spaceAfter=8,
            textColor="#000000",
        ),
        "meta": ParagraphStyle(
            "Meta", parent=base, fontName=KOREAN_FONT_NAME,
            fontSize=9, leading=12, alignment=1, spaceAfter=10,
            textColor="#000000",
        ),
        "section": ParagraphStyle(
            "Section", parent=base, fontName=KOREAN_BOLD_NAME,
            fontSize=14, leading=18, spaceBefore=10, spaceAfter=6,
            textColor="#000000", charSpace=BS,
        ),
        "section_topbar": ParagraphStyle(
            "SectionTopbar", parent=base, fontName=KOREAN_BOLD_NAME,
            fontSize=10.5, leading=14, textColor="#000000",
            spaceBefore=4, spaceAfter=6, charSpace=BS,
        ),
        "source_label": ParagraphStyle(
            "SourceLabel", parent=base, fontName=KOREAN_FONT_NAME,
            fontSize=8, textColor="#000000", leading=10,
            spaceBefore=4, spaceAfter=1, charSpace=BS,
        ),
        "qbody": ParagraphStyle(
            "QBody", parent=base, fontName=KOREAN_FONT_NAME,
            fontSize=10, leading=16, spaceAfter=3,
            textColor="#000000", charSpace=BS,
        ),
        "choice": ParagraphStyle(
            "Choice", parent=base, fontName=KOREAN_FONT_NAME,
            fontSize=10, leading=15, leftIndent=12, spaceAfter=1,
            textColor="#000000", charSpace=BS,
        ),
        "answer_line": ParagraphStyle(
            "AnswerLine", parent=base, fontName=KOREAN_BOLD_NAME,
            fontSize=11, leading=15, spaceBefore=6, spaceAfter=2,
            textColor="#000000", charSpace=BS,
        ),
        "concept_tag": ParagraphStyle(
            "ConceptTag", parent=base, fontName=KOREAN_BOLD_NAME,
            fontSize=10, textColor="#cc0000", leading=14, spaceAfter=2,
            charSpace=BS,
        ),
        "expl_body": ParagraphStyle(
            "ExplBody", parent=base, fontName=KOREAN_FONT_NAME,
            fontSize=10, leading=16, spaceAfter=4,
            textColor="#000000", charSpace=BS,
        ),
        "expl_missing": ParagraphStyle(
            "ExplMissing", parent=base, fontName=KOREAN_FONT_NAME,
            fontSize=10, textColor="#888888", leading=16, spaceAfter=4,
            charSpace=BS,
        ),
        "table_cell": ParagraphStyle(
            "TableCell", parent=base, fontName=KOREAN_FONT_NAME,
            fontSize=8.5, leading=10.5, textColor="#000000", charSpace=BS,
        ),
        "table_header": ParagraphStyle(
            "TableHeader", parent=base, fontName=KOREAN_BOLD_NAME,
            fontSize=8.5, leading=10.5, textColor="#000000", charSpace=BS,
            alignment=1,
        ),
    }


def fetch_questions(filters: dict) -> list[dict]:
    """filters: {
        year:int | list[int] | None (None=모든 연도 합침),
        subject_ids:list[int],
        qnum_ranges:dict[subject_id,(lo,hi)],
        include_hidden:bool,
        chapter_ids:list[int] (optional, 선택 단원만),
        unclassified_only:bool (optional)
    }"""
    where = []
    params: list = []
    yr = filters.get("year")
    if yr is None:
        pass  # 모든 연도
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
        where.append(
            "q.subject_id IN ({})".format(",".join("?" * len(filters["subject_ids"])))
        )
        params.extend(filters["subject_ids"])
    if not filters.get("include_hidden", True):
        where.append("q.has_image = 0")
    if filters.get("chapter_ids"):
        where.append(
            "q.chapter_id IN ({})".format(",".join("?" * len(filters["chapter_ids"])))
        )
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
            # qnum_ranges per subject
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


def render_pdf(questions: list[dict], filters: dict, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = build_styles()

    # exam_style_guide.md 3-1: A4, 좌우 18mm / 상하 18mm, 컬럼 간격 8mm
    page_w, page_h = A4
    margin = 18 * mm
    gap = 8 * mm
    col_w = (page_w - 2 * margin - gap) / 2  # 약 83mm
    body_h = page_h - 2 * margin

    left = Frame(margin, margin, col_w, body_h, leftPadding=0, rightPadding=0,
                 topPadding=0, bottomPadding=0, showBoundary=0)
    right = Frame(margin + col_w + gap, margin, col_w, body_h,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, showBoundary=0)

    doc = BaseDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
        title="국시 학습지",
    )
    doc.addPageTemplates([PageTemplate(id="2col", frames=[left, right])])
    story = []

    # 표지
    year = filters.get("year", "")
    if year is None or year == "":
        title_text = "약사 국시 기출 학습지 (5년치 통합)"
    elif isinstance(year, (list, tuple, set)):
        title_text = f"{','.join(map(str, sorted(year)))}년 약사 국시 기출 학습지"
    else:
        title_text = f"{year}년 약사 국시 기출 학습지"
    story.append(Paragraph(title_text, styles["title"]))
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    subj_names = sorted({q["subject"] for q in questions})
    meta = f"{today} · 총 {len(questions)}문제 · {' / '.join(subj_names)}"
    story.append(Paragraph(meta, styles["meta"]))

    # ========== 본문 (관련 국시 문제) — 견본 양식 ==========
    story.append(Paragraph("관련 국시 문제", styles["section_topbar"]))

    def group_label(q: dict) -> str:
        """그룹 라벨. 카테고리(상위 chapter)+단원이 있으면 둘 다, 단원만 있으면 단원만, 없으면 과목."""
        if q.get("chapter_id") and q.get("chapter_name"):
            cat = q.get("category_name")
            chap = q["chapter_name"]
            if cat:
                return f"[{q['subject']}] {cat} · {chap}"
            return f"[{q['subject']}] {chap}"
        return f"[{q['subject']}] (단원 미분류)"

    def esc(s: str) -> str:
        # 1) 단위 결합 보정 → 2) HTML escape → 3) 줄바꿈 태그 복구
        s = fix_spacing(s or "")
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # add_linebreaks는 escape 후 적용 (escape가 <br/>도 escape 해버려서)
        return add_linebreaks(s)

    def esc_with_breaks(s: str) -> str:
        """본문용: 단위 보정 + escape + 섹션 태그 줄바꿈"""
        s = fix_spacing(s or "")
        # 먼저 줄바꿈 마커 삽입 (<br/> 토큰)
        s = add_linebreaks(s)
        # 그 후 < > 만 escape (단, <br/>는 보존)
        # 트릭: <br/> → 임시 토큰 → escape → 토큰 복구
        s = s.replace("<br/>", "\x00BR\x00")
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        s = s.replace("\x00BR\x00", "<br/>")
        return s

    def esc_simple(s: str) -> str:
        """보기용: 단위 보정 + escape만 (줄바꿈 없음)"""
        s = fix_spacing(s or "")
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    current_group = None
    for idx, q in enumerate(questions, start=1):
        gl = group_label(q)
        if gl != current_group:
            current_group = gl
            story.append(Paragraph(gl, styles["section"]))

        # 단원 안에서 여러 연도가 합쳐지므로, 출처 라벨(연도·교시) + qid 표시 (사용자가 분류 수정 시 참조)
        # exam_style_guide 7번: 검정 단색 (qid 표시도 검정 유지, 작게)
        src = f"{q['year']}년도 {q['session']}교시 {q['qnum']}번  <font size='7'>[qid={q['id']}]</font>"
        block = [Paragraph(src, styles["source_label"])]

        # body를 [표] 영역 기준으로 분리 (table_restore_instruction.md)
        body_parts = split_body_with_tables(q["body"] or "")
        first_text = True
        for kind, content in body_parts:
            if kind == 'text':
                txt = esc_with_breaks(content)
                if not txt:
                    continue
                if first_text:
                    # 첫 텍스트 부분에 문제 번호 prepend
                    if q["has_image"] and '[그림 비공개]' not in txt:
                        txt = txt + " <font color='#000000'>[그림 비공개]</font>"
                    block.append(Paragraph(f"<b>{idx}.</b> {txt}", styles["qbody"]))
                    first_text = False
                else:
                    block.append(Paragraph(txt, styles["qbody"]))
            elif kind == 'table':
                # ReportLab Table — 가이드 4번: 검정 1px 외곽+셀, 헤더 굵게
                tbl_data = [
                    [Paragraph(esc_simple(cell), styles["table_header"] if i == 0 else styles["table_cell"])
                     for cell in row]
                    for i, row in enumerate(content)
                ]
                # 컬럼 폭: 첫 컬럼(보통 약물명)을 가장 넓게, 나머지 균등
                n_cols = len(content[0]) if content else 1
                if n_cols >= 3:
                    first_w = col_w * 0.55
                    rest_w = (col_w - first_w) / (n_cols - 1)
                    col_widths = [first_w] + [rest_w] * (n_cols - 1)
                elif n_cols == 2:
                    col_widths = [col_w * 0.6, col_w * 0.4]
                else:
                    col_widths = [col_w]
                t = Table(tbl_data, colWidths=col_widths, repeatRows=1, hAlign='LEFT')
                t.setStyle(TableStyle([
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                    ('TOPPADDING', (0, 0), (-1, -1), 3),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                    ('LEFTPADDING', (0, 0), (-1, -1), 3),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    # 헤더 행 굵게 표시 (배경 없음 — 가이드 4번 흰색 유지)
                    ('FONTNAME', (0, 0), (-1, 0), KOREAN_BOLD_NAME),
                ]))
                block.append(Spacer(1, 2))
                block.append(t)
                block.append(Spacer(1, 2))

        # has_image이지만 본문에 없는 경우 (텍스트 part 없는 케이스 보호)
        if first_text:
            placeholder = "<b>{}.</b>".format(idx)
            if q["has_image"]:
                placeholder += " [그림 비공개]"
            block.append(Paragraph(placeholder, styles["qbody"]))

        for n, text in q["choices"]:
            label = CHOICE_LABELS[n - 1] if 1 <= n <= 5 else f"({n})"
            block.append(Paragraph(f"{label} {esc_simple(text)}", styles["choice"]))
        block.append(Spacer(1, 4))
        story.append(KeepTogether(block))

    # ========== 정답과 해설 — 견본 양식 ==========
    story.append(PageBreak())
    story.append(Paragraph("정답과 해설", styles["title"]))
    story.append(Spacer(1, 6))

    current_group = None
    for idx, q in enumerate(questions, start=1):
        gl = group_label(q)
        if gl != current_group:
            current_group = gl
            story.append(Paragraph(gl, styles["section"]))

        ans = q["answer"]
        ans_label = CHOICE_LABELS[ans - 1] if isinstance(ans, int) and 1 <= ans <= 5 else "—"
        story.append(Paragraph(
            f"{idx}) {ans_label}  <font color='#bbbbbb' size='8'>[qid={q['id']} · {q['year']}년 {q['session']}교시 {q['qnum']}번]</font>",
            styles["answer_line"]))
        if q["concept_tag"]:
            story.append(Paragraph(esc_simple(q["concept_tag"]), styles["concept_tag"]))
        if q["explanation"]:
            story.append(Paragraph(esc_with_breaks(q["explanation"]), styles["expl_body"]))
        else:
            story.append(Paragraph("해설 미작성", styles["expl_missing"]))

    doc.build(story)
    return out_path


def make_worksheet(filters: dict) -> Path:
    qs = fetch_questions(filters)
    if not qs:
        raise ValueError("선택 조건에 해당하는 문제가 없습니다.")
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(OUTPUT_DIR) / f"worksheet_{ts}.pdf"
    render_pdf(qs, filters, out_path)
    # 이력 기록
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO worksheets (created_at, filter_json, pdf_path) VALUES (?, ?, ?)",
            (dt.datetime.now().isoformat(timespec="seconds"), json.dumps(filters, ensure_ascii=False), str(out_path)),
        )
    return out_path


if __name__ == "__main__":
    # 샘플: 2024년 생명약학 1~50번
    with get_conn() as conn:
        sb = conn.execute("SELECT id FROM subjects WHERE name = ?", ("생명약학",)).fetchone()
    filters = {
        "year": 2024,
        "subject_ids": [sb["id"]] if sb else [],
        "qnum_ranges": {sb["id"]: (1, 50)} if sb else {},
        "include_hidden": True,
    }
    p = make_worksheet(filters)
    print("생성:", p)
