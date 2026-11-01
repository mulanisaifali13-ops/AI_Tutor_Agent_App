"""Read PDF, TXT and common image formats; OCR images and scanned PDF pages."""
import io
import re
from pathlib import Path
from typing import Dict, List

import config
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError

try:
    import pymupdf
except ImportError:  # compatibility with older PyMuPDF releases
    import fitz as pymupdf

from backend.schemas import Span


class DocumentError(Exception):
    """Friendly document-processing error suitable for the UI."""


SAMPLE_TEXT = """How Plants Make Food: Photosynthesis

Plants are called producers because they make their own food. They do this with a process called photosynthesis. In photosynthesis, a plant uses light energy to turn water and carbon dioxide into glucose, a type of sugar, and oxygen. The glucose gives the plant energy to live and grow, and the oxygen is released into the air.

Light and chlorophyll

Photosynthesis happens mostly in the leaves, inside tiny structures called chloroplasts. Chloroplasts contain a green pigment called chlorophyll. Chlorophyll absorbs light, mainly red and blue light, and reflects green light, which is why leaves look green. The absorbed light energy is what powers the whole process. Without chlorophyll, a plant could not capture the energy it needs.

Raw materials: water and carbon dioxide

A plant needs two raw materials for photosynthesis. Water is taken up from the soil by the roots and carried to the leaves through tubes called xylem. Carbon dioxide enters the leaf from the air through tiny holes called stomata, which are mostly found on the underside of the leaf. Each stoma can open and close, which helps the plant control how much water it loses.

The two stages of photosynthesis

Photosynthesis has two connected stages. In the first stage, called the light-dependent reactions, chlorophyll absorbs light and the energy is used to split water molecules. This releases oxygen and stores energy in small carrier molecules. In the second stage, called the Calvin cycle, the stored energy is used to turn carbon dioxide into glucose. The Calvin cycle does not use light directly, but it depends on the energy carriers made in the first stage.

What changes the speed of photosynthesis

Several factors change how fast photosynthesis happens. More light makes the process faster until the plant cannot use any more light. Higher carbon dioxide levels also speed it up, up to a limit. Temperature matters too: the reactions are controlled by enzymes, which work best at a moderate temperature and slow down or stop if it becomes too hot. The factor that is in shortest supply is called the limiting factor, because it limits how fast the whole process can go.

What the plant does with glucose

The glucose made in photosynthesis has several uses. Some is used right away in respiration to release energy. Some is changed into starch for storage, and some is used to build cellulose for cell walls. Because plants store extra glucose as starch, we can test a leaf for starch to show that photosynthesis has been happening.
"""

SUPPORTED_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"
}
SUPPORTED_EXTENSIONS = {".pdf", ".txt"} | SUPPORTED_IMAGE_EXTENSIONS


def _ocr_image(image: Image.Image) -> str:
    """Extract printed English text from a PIL image using Tesseract OCR."""
    try:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        # Avoid excessive memory use on very high-resolution uploads.
        if max(image.size) > 5000:
            image.thumbnail((5000, 5000))
        image = ImageOps.grayscale(image)
        return pytesseract.image_to_string(
            image,
            lang="eng",
            config="--oem 3 --psm 3"
        ).strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise DocumentError(
            "The Tesseract OCR engine is not installed on this server. "
            "Install Tesseract and restart the application."
        ) from exc
    except Exception as exc:
        raise DocumentError(f"OCR could not process this image: {exc}") from exc


def _read_image(file_bytes: bytes) -> List[Dict]:
    """OCR a single image. Animated GIF and multi-page TIFF frames are supported."""
    pages: List[Dict] = []
    try:
        with Image.open(io.BytesIO(file_bytes)) as image:
            frame_count = getattr(image, "n_frames", 1)
            # Prevent unexpectedly large animated/multi-page files from exhausting memory.
            if frame_count > 100:
                raise DocumentError("This image contains more than 100 frames/pages.")
            for frame_index in range(frame_count):
                image.seek(frame_index)
                frame = image.copy()
                pages.append({"page": frame_index + 1, "text": _ocr_image(frame)})
                frame.close()
    except DocumentError:
        raise
    except UnidentifiedImageError as exc:
        raise DocumentError("This image is invalid or its format is unsupported.") from exc
    except Exception as exc:
        raise DocumentError(f"I could not open this image: {exc}") from exc
    return pages


def _read_pdf(file_bytes: bytes) -> List[Dict]:
    """Extract selectable PDF text and OCR scanned/image-only pages."""
    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted:
            doc.close()
            raise DocumentError("This PDF is password protected. Upload an unlocked PDF.")
        pages: List[Dict] = []
        for index, page in enumerate(doc):
            text = page.get_text("text").strip()
            # A page with little/no selectable text is probably scanned.
            if len(text) < 30:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                with Image.open(io.BytesIO(pix.tobytes("png"))) as rendered:
                    text = _ocr_image(rendered)
            pages.append({"page": index + 1, "text": text})
        doc.close()
        return pages
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(
            "I could not open this PDF. It may be damaged or password protected."
        ) from exc


def read_document(file_bytes: bytes, filename: str) -> List[Dict]:
    """Detect the uploaded file by extension and return a common page/text structure."""
    if not file_bytes:
        raise DocumentError("The uploaded file is empty.")

    extension = Path(filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise DocumentError(f"Unsupported file type. Supported formats: {allowed}")

    if extension == ".pdf":
        pages = _read_pdf(file_bytes)
    elif extension == ".txt":
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = file_bytes.decode("utf-8", errors="replace")
        pages = [{"page": 1, "text": text}]
    else:
        pages = _read_image(file_bytes)

    if not pages:
        raise DocumentError("This file does not contain any readable pages.")
    return pages


def check_quality(pages: List[Dict]) -> None:
    """Reject files that contain too little extractable text for useful tutoring."""
    total = sum(len((page.get("text") or "").strip()) for page in pages)
    average = total / max(len(pages), 1)
    if total < config.MIN_TOTAL_CHARS or average < config.MIN_AVG_CHARS:
        raise DocumentError(
            "I could not find enough readable text in this file. "
            "Try a clearer, higher-resolution image or a document with more text. "
            "Printed text works best; handwriting and complex equations may not OCR accurately."
        )


def _split_long(text: str, max_len: int) -> List[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        while len(s) > max_len:                       # no sentence end found: hard cut
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(s[:max_len])
            s = s[max_len:]
        if cur and len(cur) + len(s) + 1 > max_len:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def make_spans(pages: List[Dict], target: int = config.SPAN_TARGET, max_len: int = config.SPAN_MAX) -> List[Span]:
    spans: List[Span] = []

    def add(page: int, text: str) -> None:
        if text.strip():
            spans.append(Span(id=f"S{len(spans) + 1}", page=page, text=text.strip()))

    for p in pages:
        paras = [re.sub(r"\s*\n\s*", " ", x).strip() for x in re.split(r"\n\s*\n", p["text"])]
        buf = ""
        for para in filter(None, paras):
            pieces = _split_long(para, max_len) if len(para) > max_len else [para]
            for piece in pieces:
                if buf and len(buf) + len(piece) + 2 > target:
                    add(p["page"], buf)
                    buf = piece
                else:
                    buf = f"{buf}\n\n{piece}".strip()
        add(p["page"], buf)
    return spans
