"""OCR helpers: PDF page → image → macOS Vision OCR → text/bbox annotations."""
from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from ocrmac import ocrmac
from PIL import Image


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


@dataclass
class Box:
    text: str
    confidence: float
    # Normalized coordinates. macOS Vision uses bottom-left origin.
    x: float       # left (0..1)
    y_bottom: float  # bottom (0..1)
    w: float
    h: float

    @property
    def y_top(self) -> float:
        # convert to top-down (1 - top)
        return 1.0 - (self.y_bottom + self.h)

    @property
    def y_center(self) -> float:
        return self.y_top + self.h / 2

    @property
    def x_center(self) -> float:
        return self.x + self.w / 2


def page_to_image(pdf_path: Path | str, page_index: int, dpi: int = 220) -> Image.Image:
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi)
        return Image.open(io.BytesIO(pix.tobytes("png"))).copy()
    finally:
        doc.close()


def ocr_image(img: Image.Image, languages: list[str] | None = None) -> list[Box]:
    """Run macOS Vision OCR on a PIL image, return Box list."""
    if languages is None:
        languages = ["ko-KR", "en-US"]
    # ocrmac expects file path or PIL image
    annotations = ocrmac.OCR(img, language_preference=languages).recognize()
    boxes: list[Box] = []
    for text, conf, bbox in annotations:
        x, y_bottom, w, h = bbox
        boxes.append(Box(text=nfc(text), confidence=conf, x=x, y_bottom=y_bottom, w=w, h=h))
    return boxes


def ocr_pdf_page(pdf_path: Path | str, page_index: int, dpi: int = 220,
                 languages: list[str] | None = None) -> list[Box]:
    img = page_to_image(pdf_path, page_index, dpi=dpi)
    return ocr_image(img, languages=languages)


def group_into_rows(boxes: list[Box], y_tolerance: float = 0.012) -> list[list[Box]]:
    """Group boxes into rows by y_center. Returns rows sorted top→bottom, each row sorted left→right."""
    if not boxes:
        return []
    sorted_boxes = sorted(boxes, key=lambda b: b.y_center)
    rows: list[list[Box]] = []
    current: list[Box] = [sorted_boxes[0]]
    current_y = sorted_boxes[0].y_center
    for b in sorted_boxes[1:]:
        if abs(b.y_center - current_y) <= y_tolerance:
            current.append(b)
            # update centroid
            current_y = sum(x.y_center for x in current) / len(current)
        else:
            rows.append(sorted(current, key=lambda x: x.x_center))
            current = [b]
            current_y = b.y_center
    rows.append(sorted(current, key=lambda x: x.x_center))
    return rows
