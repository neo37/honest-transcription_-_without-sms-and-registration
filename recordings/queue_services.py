# Очереди транскрибации и эмбеддингов, обработка задач
import logging
from django.db import transaction

from .models import Recording, TranscribeQueue, EmbeddingQueue

logger = logging.getLogger(__name__)


def enqueue_transcribe(recording: Recording, priority: int = 0):
    """Добавить запись в очередь транскрибации. priority=1 — по кнопке (в приоритете)."""
    TranscribeQueue.objects.update_or_create(
        recording=recording,
        defaults={'priority': priority},
    )


def enqueue_embedding(recording: Recording):
    """Добавить запись в очередь индексации эмбеддинга."""
    if not recording.transcription or not recording.transcription.strip():
        return
    EmbeddingQueue.objects.get_or_create(recording=recording)


def get_next_transcribe_task():
    """Взять следующую задачу транскрибации (с блокировкой строки)."""
    return (
        TranscribeQueue.objects
        .select_for_update(skip_locked=True)
        .order_by('-priority', 'created_at')
        .first()
    )


def get_next_embedding_task():
    """Взять следующую задачу эмбеддинга (с блокировкой строки)."""
    return (
        EmbeddingQueue.objects
        .select_for_update(skip_locked=True)
        .order_by('created_at')
        .first()
    )


def index_embedding_for_recording(recording: Recording) -> bool:
    """
    Посчитать эмбеддинг транскрипции и сохранить в RecordingEmbedding.
    Возвращает True при успехе.
    """
    from django.db import connection
    if connection.vendor != 'postgresql':
        return False
    try:
        from .models import RecordingEmbedding
    except (ImportError, AttributeError):
        return False
    text = (recording.transcription or '').strip()
    if not text:
        return False
    try:
        from sentence_transformers import SentenceTransformer
        from django.conf import settings
        model_name = getattr(settings, 'EMBEDDING_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2')
        import torch as _torch
        _emb_device = 'cuda' if _torch.cuda.is_available() else 'cpu'
        try:
            model = SentenceTransformer(model_name, device=_emb_device)
        except (RuntimeError, Exception) as _cuda_err:
            if _emb_device == 'cuda':
                logger.warning("GPU недоступен для эмбеддингов (%s), падаем на CPU", _cuda_err)
                model = SentenceTransformer(model_name, device='cpu')
            else:
                raise
        emb = model.encode(text, convert_to_numpy=True)
        if emb is None or len(emb) != 384:
            return False
        RecordingEmbedding.objects.update_or_create(
            recording=recording,
            defaults={'embedding': emb.tolist()},
        )
        return True
    except Exception as e:
        logger.exception('Embedding index failed for recording %s: %s', recording.pk, e)
        return False
