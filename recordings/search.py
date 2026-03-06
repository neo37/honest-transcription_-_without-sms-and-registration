# Полнотекстовый и семантический поиск по записям
from django.db import connection


def fulltext_search(queryset, query, search_comments=False):
    """Полнотекстовый поиск по transcription (и опционально по комментариям). Только PostgreSQL."""
    if not query or connection.vendor != 'postgresql':
        return queryset.none()
    from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
    config = 'simple'  # универсально для любого языка
    vector = SearchVector('transcription', config=config)
    if search_comments:
        vector = vector + SearchVector('comments__text', config=config)
    search_query = SearchQuery(query.strip(), config=config)
    return (
        queryset.annotate(
            search=vector,
            rank=SearchRank(vector, search_query),
        )
        .filter(search=search_query)
        .order_by('-rank', '-created_at')
        .distinct()
    )


def semantic_search(query, limit=50, min_score=0.0):
    """
    Поиск по смыслу (эмбеддинги). Возвращает list of (Recording, score).
    min_score: минимальная релевантность 0..1 — результаты с score < min_score отфильтровываются.
    """
    if not query or not query.strip():
        return []
    try:
        from .models import RecordingEmbedding
    except (ImportError, AttributeError):
        return []
    if connection.vendor != 'postgresql':
        return []
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return []
    from django.conf import settings
    model_name = getattr(settings, 'EMBEDDING_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2')
    try:
        model = SentenceTransformer(model_name)
        emb = model.encode(query.strip(), convert_to_numpy=True)
        if emb is None or len(emb) != 384:
            return []
    except Exception:
        return []
    from pgvector.django import CosineDistance
    # Берём с запасом, потом отфильтруем по min_score
    fetch_limit = min(limit * 3, 300) if min_score > 0 else limit
    qs = (
        RecordingEmbedding.objects
        .exclude(embedding__isnull=True)
        .select_related('recording')
        .annotate(distance=CosineDistance('embedding', emb.tolist()))
        .order_by('distance')[:fetch_limit]
    )
    try:
        result = [(e.recording, max(0, 1 - float(e.distance))) for e in qs]
        if min_score > 0:
            result = [(rec, s) for rec, s in result if s >= min_score][:limit]
        else:
            result = result[:limit]
        return result
    except Exception:
        return []
