"""Reads the text in a screenshot, locally (RapidOCR on the CPU, so nothing is sent anywhere).

The OCR models (about 30 MB) are downloaded the first time a screenshot is read and then reused. To
download them ahead of time, for example while building a server image, run: python -m app.services.ocr
"""

import io
import logging
import threading

from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.job_extract import TextLine

logger = logging.getLogger(__name__)

# A small file can still decode to a huge bitmap. 40 MP covers a full-page screenshot on a 4K screen.
MAX_PIXELS = 40_000_000


class InvalidImageError(Exception):
    pass


class OcrUnavailableError(Exception):
    pass


class LocalOcr:
    def __init__(self):
        self._engine = None
        # RapidOCR keeps per-call settings on the engine, so screenshots are read one at a time.
        self._lock = threading.Lock()

    def read_lines(self, data: bytes) -> list[TextLine]:
        image = _open_image(data)
        with self._lock:
            engine = self._load_engine()
            try:
                result = engine(image)
            except Exception as exc:  # the engine raises a mix of its own, numpy and onnxruntime errors
                logger.warning("OCR failed: %s", type(exc).__name__)
                raise OcrUnavailableError() from exc
        if result.boxes is None or result.txts is None:
            return []
        lines = []
        for box, text in zip(result.boxes, result.txts):
            xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
            lines.append(TextLine(text=text, left=min(xs), top=min(ys), right=max(xs), height=max(ys) - min(ys)))
        return lines

    def _load_engine(self):
        if self._engine is None:
            try:
                from rapidocr import RapidOCR  # slow import; only paid when the first screenshot is read

                # Tall full-page screenshots keep readable text instead of being shrunk to 2000 px.
                self._engine = RapidOCR(params={"Global.log_level": "warning", "Global.max_side_len": 4000})
            except Exception as exc:  # missing package, failed model download, broken onnxruntime
                logger.warning("OCR engine unavailable: %s", type(exc).__name__)
                raise OcrUnavailableError() from exc
        return self._engine


def _open_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > MAX_PIXELS:
            raise InvalidImageError()
        image = ImageOps.exif_transpose(image)
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidImageError() from exc
    if image.mode in ("RGBA", "LA", "P"):
        # Transparent areas would turn black; screenshots are read as text on white.
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.getchannel("A"))
        return background
    return image.convert("RGB")


if __name__ == "__main__":
    # Downloads the models (if needed) and checks the engine runs.
    blank = Image.new("RGB", (400, 120), "white")
    buffer = io.BytesIO()
    blank.save(buffer, format="PNG")
    LocalOcr().read_lines(buffer.getvalue())
    print("OCR is ready.")
