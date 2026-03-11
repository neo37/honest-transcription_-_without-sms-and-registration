"""
Сервис OCR на базе olmOCR / EasyOCR / Tesseract.
Принимает POST /ocr с файлом, возвращает {"markdown": "..."}.

Режимы:
- auto (default): если GPU-режим включён в настройках → EasyOCR GPU,
  иначе если OCR_SERVER задан → olmOCR (cloud), иначе → Tesseract CPU
- tesseract: Tesseract CPU
- easyocr: EasyOCR (GPU если доступен)
- olmocr: внешний olmOCR inference

Настройка GPU-режима: переменная среды OCR_USE_GPU=1 (задаётся из системных настроек)
"""
import os
import subprocess
import uuid
from pathlib import Path
from functools import lru_cache

from fastapi import FastAPI, File, HTTPException, UploadFile

app = FastAPI(title="OCR", version="0.2")

# Кэш EasyOCR reader (загружается один раз)
_easyocr_reader = None


def _get_easyocr_reader(gpu: bool):
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        _easyocr_reader = easyocr.Reader(['ru', 'en'], gpu=gpu)
    return _easyocr_reader


_FLAG_PATH = os.path.join(os.environ.get('DATA_ROOT', '/app/data'), 'ocr_gpu_mode')


def _is_gpu_mode() -> bool:
    """Читаем файл-флаг из shared volume (обновляется веб-сервером при смене настройки)."""
    try:
        return Path(_FLAG_PATH).read_text().strip() == '1'
    except Exception:
        return os.environ.get('OCR_USE_GPU', '0').strip() == '1'

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


def ocr_with_easyocr(input_path: Path) -> str:
    """EasyOCR — поддерживает GPU. Для изображений и PDF (конвертируем через pdf2image)."""
    gpu = _is_gpu_mode()
    reader = _get_easyocr_reader(gpu=gpu)
    suffix = input_path.suffix.lower()

    if suffix == ".pdf":
        from pdf2image import convert_from_path
        pages = convert_from_path(str(input_path), dpi=200)
        parts = []
        for page in pages:
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                page.save(tmp.name)
                results = reader.readtext(tmp.name, detail=0, paragraph=True)
                os.unlink(tmp.name)
            parts.append('\n'.join(results))
        return '\n\n'.join(parts)
    else:
        results = reader.readtext(str(input_path), detail=0, paragraph=True)
        return '\n'.join(results)


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
        elif method == "easyocr":
            markdown = ocr_with_easyocr(input_path)
        elif method == "auto":
            if _is_gpu_mode():
                markdown = ocr_with_easyocr(input_path)
            else:
                markdown = run_olmocr(input_path, workspace)
        else:
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
