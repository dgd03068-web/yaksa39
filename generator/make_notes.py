"""5 batch 정리노트 MD → 통합 + PDF 변환.

실행:
    python -m generator.make_notes
출력:
    app/output/study_notes_YYYYMMDD.pdf (+ .md)
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import OUTPUT_DIR  # noqa: E402

SRC_DIR = Path("/tmp/note_inline")
BATCHES = [SRC_DIR / f"notes_{i}.md" for i in range(1, 6)]


HEAD_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; @bottom-center { content: counter(page) " / " counter(pages); font-size: 9pt; color: #888; } }
body { font-family: 'Pretendard', -apple-system, sans-serif; font-size: 10.5pt; line-height: 1.65; color: #1a1a1a; word-break: keep-all; line-break: strict; }
h1 { font-size: 22pt; text-align: center; margin: 12mm 0 6mm; }
h1 + .meta { text-align: center; color: #888; font-size: 10pt; margin-bottom: 14mm; }
h2 { font-size: 12.5pt; margin: 7mm 0 2mm; padding: 3mm 4mm; background: #f7f7f7; border-left: 3pt solid #cc0000; border-radius: 2pt; page-break-after: avoid; }
h3 { font-size: 11pt; margin: 4mm 0 1.5mm; color: #444; }
p { margin: 1mm 0; }
hr { border: none; border-top: 0.5pt dashed #ddd; margin: 5mm 0; }
strong { color: #cc0000; }
code { font-family: 'Pretendard', sans-serif; background: #f0f0f0; padding: 0 3px; border-radius: 2pt; font-size: 9.5pt; }
"""


def main():
    # 1. 5 batch MD 통합
    parts = []
    for f in BATCHES:
        if f.exists():
            parts.append(f.read_text(encoding="utf-8"))
        else:
            print(f"⚠️ {f} 없음")
    body = "\n\n---\n\n".join(parts)

    today = datetime.now().strftime("%Y-%m-%d")
    header = f"# 약사국시 단원별 핵심 정리노트\n\n*5년 기출 분석 기반 · 113단원 · 회독용*  \n*생성일: {today}*"
    full_md = f"{header}\n\n---\n\n{body}"

    md_path = OUTPUT_DIR / f"study_notes_{datetime.now().strftime('%Y%m%d')}.md"
    md_path.write_text(full_md, encoding="utf-8")
    print(f"✅ MD: {md_path} ({md_path.stat().st_size//1024} KB)")

    # 2. MD → HTML
    try:
        import markdown
    except ImportError:
        print("⚠️ markdown 미설치 — pip install markdown")
        return
    html_body = markdown.markdown(full_md, extensions=["extra"])
    full_html = (
        f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        f"<style>{HEAD_CSS}</style></head><body>{html_body}</body></html>"
    )

    # 3. HTML → PDF (WeasyPrint)
    try:
        from weasyprint import HTML
    except ImportError:
        print("⚠️ weasyprint 미설치 — pip install weasyprint")
        return
    pdf_path = OUTPUT_DIR / f"study_notes_{datetime.now().strftime('%Y%m%d')}.pdf"
    HTML(string=full_html).write_pdf(pdf_path)
    print(f"✅ PDF: {pdf_path} ({pdf_path.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
