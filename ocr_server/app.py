"""
Сервис OCR на базе olmOCR (https://github.com/allenai/olmocr).
Принимает POST /ocr с файлом, возвращает {"markdown": "..."}.
Если заданы OCR_SERVER, OCR_MODEL, OCR_API_KEY — используется внешний inference (без GPU).
"""
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

app = FastAPI(title="OCR (olmOCR)", version="0.1")

WORKSPACE_BASE = Path(os.environ.get("OCR_WORKSPACE_BASE", "/tmp/ocr_workspace"))


def ocr_with_tesseract(input_path: Path) -> str:
    """Tesseract OCR для изображений и PDF (без GPU, работает локально)."""
    import pytesseract
    from PIL import Image

    suffix = input_path.suffix.lower()
    lang = os.environ.get("TESSERACT_LANG", "rus+eng")

    if suffix == ".pdf":
        from pdf2image import convert_from_path
        pages = convert_from_path(str(input_path), dpi=200)
        parts = []
        for page in pages:
            text = pytesseract.image_to_string(page, lang=lang)
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)
    else:
        img = Image.open(input_path)
        return pytesseract.image_to_string(img, lang=lang).strip()


def run_olmocr(input_path: Path, workspace: Path) -> str:
    """Запуск olmocr.pipeline (только при заданном OCR_SERVER). Возвращает markdown или пустую строку."""
    server = os.environ.get("OCR_SERVER", "").strip()
    model = os.environ.get("OCR_MODEL", "allenai/olmOCR-2-7B-1025-FP8")
    api_key = os.environ.get("OCR_API_KEY", "").strip()

    if not server:
        # Нет внешнего сервера — используем Tesseract
        return ocr_with_tesseract(input_path)

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="API ключ не задан. Для внешнего OCR укажите OCR_API_KEY в .env на сервере (например ключ Hugging Face).",
        )

    cmd = [
        "python", "-m", "olmocr.pipeline", str(workspace),
        "--markdown",
        "--pdfs", str(input_path),
        "--server", server,
        "--model", model,
        "--api_key", api_key,
    ]

    try:
        subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("OCR_TIMEOUT", "600")),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Таймаут обработки olmOCR")
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="olmOCR не установлен или не в PATH. Задайте OCR_SERVER для внешнего inference.",
        )

    md_dir = workspace / "markdown"
    if not md_dir.exists():
        return ""

    parts = []
    for f in sorted(md_dir.glob("*.md")):
        parts.append(f.read_text(encoding="utf-8", errors="replace"))
    return "\n\n".join(parts)


@app.get("/health")
def health():
    return {"status": "ok"}


MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "application/pdf": ".pdf",
}

@app.post("/ocr")
async def ocr(file: UploadFile = File(...), method: str = "auto"):
    """Принять файл (PDF/PNG/JPEG), вернуть распознанный Markdown.
    method: "auto" | "tesseract" | "olmocr"
    """
    suffix = Path(file.filename or "doc").suffix.lower()
    # Fallback to MIME type if extension unknown (e.g. mobile camera uploads)
    if suffix not in (".pdf", ".png", ".jpg", ".jpeg"):
        ct = (file.content_type or "").split(";")[0].strip().lower()
        suffix = MIME_TO_EXT.get(ct, suffix)
    if suffix not in (".pdf", ".png", ".jpg", ".jpeg"):
        raise HTTPException(400, "Разрешены только PDF, PNG, JPEG")

    work_id = uuid.uuid4().hex[:12]
    workspace = WORKSPACE_BASE / work_id
    workspace.mkdir(parents=True, exist_ok=True)
    input_path = workspace / f"doc{suffix}"

    try:
        content = await file.read()
        input_path.write_bytes(content)
    except Exception as e:
        raise HTTPException(500, f"Ошибка сохранения файла: {e}")

    try:
        if method == "tesseract":
            markdown = ocr_with_tesseract(input_path)
        else:
            # olmocr или auto: run_olmocr сам выбирает tesseract если OCR_SERVER не задан
            markdown = run_olmocr(input_path, workspace)
        return {"markdown": markdown, "method": method}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))
    finally:
        try:
            import shutil
            shutil.rmtree(workspace, ignore_errors=True)
        except Exception:
            pass
