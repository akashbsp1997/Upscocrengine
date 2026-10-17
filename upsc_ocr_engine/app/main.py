from __future__ import annotations

import io
import re
from typing import Any

import pymupdf
import pytesseract
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps, ImageFilter, UnidentifiedImageError
from pytesseract import Output

app = FastAPI(
    title="UPSC OCR Engine",
    description="Self-hosted OCR API for scanned PDFs and images. No AI model/API calls.",
    version="1.0.0",
)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 30
ALLOWED_LANGUAGES = {"eng", "hin", "eng+hin"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "UPSC OCR Engine", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        version = str(pytesseract.get_tesseract_version())
        return {"status": "ok", "tesseract_version": version}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Tesseract is unavailable: {exc}")


def preprocess(image: Image.Image) -> Image.Image:
    """Basic, explainable preprocessing. Does not call an AI service."""
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    # Upscale small images to help recognition.
    if image.width < 1600:
        scale = min(2.0, 1600 / max(image.width, 1))
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            Image.Resampling.LANCZOS,
        )
    image = image.filter(ImageFilter.SHARPEN)
    # Convert to high-contrast black and white.
    image = image.point(lambda px: 255 if px > 175 else 0)
    return image


def recognize(image: Image.Image, language: str) -> dict[str, Any]:
    processed = preprocess(image)
    config = "--oem 3 --psm 3"
    text = pytesseract.image_to_string(processed, lang=language, config=config).strip()
    data = pytesseract.image_to_data(
        processed, lang=language, config=config, output_type=Output.DICT
    )
    confidences = []
    for raw in data.get("conf", []):
        try:
            value = float(raw)
            if value >= 0:
                confidences.append(value)
        except (TypeError, ValueError):
            pass
    confidence = round(sum(confidences) / len(confidences), 1) if confidences else None
    return {
        "text": text,
        "ocr_confidence": confidence,
        "recognition_method": "tesseract_ocr",
        "image_width": image.width,
        "image_height": image.height,
    }


def structure_text(text: str) -> dict[str, Any]:
    """
    Heuristic structure only, not AI summarization.
    Keeps the extracted text intact and suggests headings/bullets using simple rules.
    """
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    headings: list[str] = []
    bullet_points: list[str] = []
    paragraphs: list[str] = []

    bullet_re = re.compile(r"^(?:[-*•▪◦]|\d+[.)]|[A-Z][.)])\s+")
    for line in lines:
        if bullet_re.match(line):
            bullet_points.append(bullet_re.sub("", line).strip())
        elif (
            len(line) <= 90
            and len(line.split()) <= 12
            and not re.search(r"[.!?]$", line)
            and (line.isupper() or line.istitle() or line.endswith(":"))
        ):
            headings.append(line.rstrip(":"))
        else:
            paragraphs.append(line)

    return {
        "headings": headings,
        "bullet_points": bullet_points,
        "paragraphs": paragraphs,
        "note": "These are rule-based suggestions, not an AI-generated summary. Check OCR output against the source.",
    }


async def read_upload(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 15 MB upload limit.")
    return data


@app.post("/api/read")
async def read_document(
    file: UploadFile = File(...),
    language: str = Form("eng"),
) -> dict[str, Any]:
    """
    Upload a scanned PDF or image.
    Returns page-ordered OCR text plus heuristic structured notes.
    Try this endpoint from /docs.
    """
    if language not in ALLOWED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language setting. Choose one of: {', '.join(sorted(ALLOWED_LANGUAGES))}",
        )

    filename = (file.filename or "").lower()
    data = await read_upload(file)
    pages: list[dict[str, Any]] = []

    try:
        if data.startswith(b"%PDF-") or filename.endswith(".pdf"):
            try:
                document = pymupdf.open(stream=data, filetype="pdf")
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Could not open PDF: {exc}")

            with document:
                if document.is_encrypted:
                    raise HTTPException(status_code=400, detail="Password-protected PDFs are not supported.")
                if len(document) > MAX_PDF_PAGES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"PDF has {len(document)} pages; limit is {MAX_PDF_PAGES} pages per request.",
                    )
                for index, page in enumerate(document):
                    # Use existing text when the PDF has a real text layer.
                    native_text = page.get_text("text").strip()
                    if len(native_text) >= 30:
                        result = {
                            "text": native_text,
                            "ocr_confidence": None,
                            "recognition_method": "pdf_text_layer",
                            "image_width": None,
                            "image_height": None,
                        }
                    else:
                        pix = page.get_pixmap(dpi=240, colorspace=pymupdf.csRGB, alpha=False)
                        image = Image.open(io.BytesIO(pix.tobytes("png")))
                        result = recognize(image, language)
                    pages.append({
                        "page_number": index + 1,
                        **result,
                        "structured": structure_text(result["text"]),
                    })
        else:
            try:
                image = Image.open(io.BytesIO(data))
                image.load()
            except (UnidentifiedImageError, OSError):
                raise HTTPException(
                    status_code=415,
                    detail="Unsupported file. Upload a PDF, JPG, PNG, TIFF, BMP or WebP image.",
                )
            # Process every frame of multi-page TIFF, but limit work.
            frame_count = getattr(image, "n_frames", 1)
            if frame_count > MAX_PDF_PAGES:
                raise HTTPException(status_code=413, detail="Multi-page image exceeds 30 frames.")
            for index in range(frame_count):
                image.seek(index)
                frame = image.copy()
                result = recognize(frame, language)
                pages.append({
                    "page_number": index + 1,
                    **result,
                    "structured": structure_text(result["text"]),
                })
    finally:
        await file.close()

    combined_text = "\n\n".join(
        f"--- Page {page['page_number']} ---\n{page['text']}" for page in pages
    )
    return {
        "filename": upload.filename,
        "page_count": len(pages),
        "language": language,
        "pages": pages,
        "combined_text": combined_text,
        "processing": {
            "ocr_engine": "Tesseract",
            "ai_api_used": False,
            "max_upload_bytes": MAX_UPLOAD_BYTES,
        },
    }
