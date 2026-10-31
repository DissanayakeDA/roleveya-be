"""Reads the text of a CV PDF. PDFs without a text layer (scans, exported images) go through the local OCR."""

import logging

from app.services.ocr import LocalOcr, OcrUnavailableError

logger = logging.getLogger(__name__)

MAX_PAGES = 5
# Fewer letters than this per page means the page is an image, not text.
MIN_TEXT_PER_PAGE = 40


class InvalidPdfError(Exception):
    pass


def pdf_text(data: bytes, ocr: LocalOcr | None = None) -> str:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:  # damaged or password-protected
        raise InvalidPdfError() from exc
    try:
        pages = [document[index] for index in range(min(len(document), MAX_PAGES))]
        texts = [_page_text(page) for page in pages]
        letters = sum(sum(char.isalpha() for char in text) for text in texts)
        if ocr is not None and letters < MIN_TEXT_PER_PAGE * max(len(pages), 1):
            texts = [_ocr_page(page, ocr) for page in pages]
        return "\n\n".join(text.strip() for text in texts if text.strip())
    finally:
        document.close()


def _page_text(page) -> str:
    textpage = page.get_textpage()
    try:
        return textpage.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n")
    finally:
        textpage.close()


def _ocr_page(page, ocr: LocalOcr) -> str:
    import io

    image = page.render(scale=2).to_pil()
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    try:
        lines = ocr.read_lines(buffer.getvalue())
    except OcrUnavailableError:
        logger.warning("OCR unavailable for an image-only PDF page")
        return ""
    # Group boxes into rows by their vertical position, left to right within a row.
    rows: list[list] = []
    for line in sorted(lines, key=lambda item: (item.top, item.left)):
        if rows and abs(rows[-1][0].top - line.top) < max(line.height, 1) * 0.6:
            rows[-1].append(line)
        else:
            rows.append([line])
    return "\n".join(" ".join(item.text for item in sorted(row, key=lambda item: item.left)) for row in rows)
