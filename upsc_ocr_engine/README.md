# UPSC OCR Engine (no AI API)

A small FastAPI backend for scanned UPSC PDFs and images. It uses Tesseract OCR and PyMuPDF locally on the server; it does not send documents to an AI provider.

## Features

- `POST /api/read`: upload PDF or image and get page-ordered text plus heuristic structure
- `GET /health`: check the service and Tesseract installation
- PDF pages with a text layer use that text; image-only/scanned pages are rendered and OCR'd
- JPG, JPEG, PNG, TIFF, BMP and WebP images
- English (`eng`), Hindi (`hin`) or English + Hindi (`eng+hin`)
- 15 MB upload limit and 30-page/frame processing limit
- Swagger API tester at `/docs`

## Run locally / in a Linux Codespace

Tesseract system packages are required. The easiest path is Docker:

```bash
docker build -t upsc-ocr .
docker run --rm -p 8000:8000 upsc-ocr
```

Then open `http://localhost:8000/docs` in the Codespaces browser preview or port-forwarding UI. Use `GET /health`, then `POST /api/read`.

If not using Docker, install Tesseract plus English and Hindi language data on the Linux environment, then:

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Test the API

With a sample file in the terminal:

```bash
curl -X POST "http://127.0.0.1:8000/api/read"   -F "file=@sample-scan.pdf"   -F "language=eng"
```

Use `language=hin` for Hindi or `language=eng+hin` for mixed Hindi-English pages. Language packs must be installed.

Example response shape:

```json
{
  "filename": "sample-scan.pdf",
  "page_count": 1,
  "language": "eng",
  "pages": [
    {
      "page_number": 1,
      "text": "Extracted text...",
      "ocr_confidence": 87.2,
      "recognition_method": "tesseract_ocr",
      "structured": {
        "headings": ["Example Heading"],
        "bullet_points": ["First point"],
        "paragraphs": ["A paragraph..."],
        "note": "These are rule-based suggestions..."
      }
    }
  ],
  "combined_text": "--- Page 1 ---\nExtracted text...",
  "processing": {
    "ocr_engine": "Tesseract",
    "ai_api_used": false,
    "max_upload_bytes": 15728640
  }
}
```

## Deploy

Deploy the Dockerfile to a container host that supports persistent service hosting. Set the port to `8000` or use the host's `$PORT` convention if required. Do not publish sensitive documents in public repositories.

For a production deployment, add authentication, rate limiting, request timeouts, monitoring, temporary-file cleanup, and a queue for large PDFs. This starter keeps uploads in memory and does not save documents to disk.

## Accuracy notes

- OCR is not perfect. Low-resolution scans, skewed pages, columns, tables, handwriting and mixed scripts can produce errors.
- `structured` is a simple heuristic classification, not a semantic summary.
- For more languages, install the matching Tesseract trained data and add the language code to `ALLOWED_LANGUAGES`.
- Respect copyright and privacy when processing or redistributing study material.
