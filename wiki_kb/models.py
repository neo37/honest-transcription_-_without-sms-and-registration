import uuid
import logging
from django.db import models

logger = logging.getLogger(__name__)


class WikiArticle(models.Model):
    title = models.CharField('Заголовок', max_length=256)
    slug = models.SlugField('Slug', max_length=256, unique=True)
    content = models.TextField('Содержимое (Markdown)', blank=True, default='')
    parent = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='children',
        verbose_name='Родительская статья',
    )
    space = models.ForeignKey(
        'recordings.Space', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='wiki_articles',
        verbose_name='Пространство',
    )
    recordings = models.ManyToManyField(
        'recordings.Recording', blank=True,
        related_name='wiki_articles',
        verbose_name='Связанные записи',
    )
    created_by = models.ForeignKey(
        'recordings.SiteUser', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='created_wiki_articles',
        verbose_name='Создал',
    )
    updated_by = models.ForeignKey(
        'recordings.SiteUser', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='updated_wiki_articles',
        verbose_name='Изменил',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)
    order = models.IntegerField('Порядок', default=0)
    is_deleted = models.BooleanField(default=False)
    share_token = models.UUIDField('Токен публичного доступа', null=True, blank=True, unique=True)

    class Meta:
        verbose_name = 'Статья'
        verbose_name_plural = 'Статьи'
        ordering = ['order', 'title']

    def __str__(self):
        return self.title

    def get_ancestors(self):
        """Список предков от корня до родителя."""
        ancestors = []
        current = self.parent
        while current:
            ancestors.insert(0, current)
            current = current.parent
        return ancestors

    def get_children(self):
        return self.children.filter(is_deleted=False).order_by('order', 'title')

    def get_all_descendants_ids(self):
        """IDs всех потомков (для удаления дерева)."""
        ids = []
        stack = list(self.children.filter(is_deleted=False).values_list('pk', flat=True))
        while stack:
            pk = stack.pop()
            ids.append(pk)
            child = WikiArticle.objects.filter(pk=pk).first()
            if child:
                stack.extend(child.children.filter(is_deleted=False).values_list('pk', flat=True))
        return ids


class WikiRevision(models.Model):
    article = models.ForeignKey(
        WikiArticle, on_delete=models.CASCADE, related_name='revisions',
        verbose_name='Статья',
    )
    content = models.TextField('Содержимое')
    revised_by = models.ForeignKey(
        'recordings.SiteUser', null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name='Автор правки',
    )
    created_at = models.DateTimeField('Время правки', auto_now_add=True)
    comment = models.CharField('Комментарий', max_length=256, blank=True, default='')

    class Meta:
        verbose_name = 'Ревизия'
        verbose_name_plural = 'Ревизии'
        ordering = ['-created_at']

    def __str__(self):
        return f'Ревизия {self.article.title} — {self.created_at}'


class WikiArticleEmbedding(models.Model):
    """Устаревшая модель — оставлена для совместимости. Используйте WikiArticleChunk."""
    article = models.OneToOneField(
        WikiArticle, on_delete=models.CASCADE,
        related_name='embedding', verbose_name='Статья',
    )
    embedding = models.JSONField('Вектор')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Эмбеддинг статьи (устар.)'
        verbose_name_plural = 'Эмбеддинги статей (устар.)'


class WikiArticleChunk(models.Model):
    """
    Чанк (фрагмент) статьи с отдельным эмбеддингом.
    Длинные статьи разбиваются на несколько чанков с перекрытием.
    """
    article = models.ForeignKey(
        WikiArticle, on_delete=models.CASCADE,
        related_name='chunks', verbose_name='Статья',
    )
    chunk_index = models.SmallIntegerField('Индекс чанка', default=0)
    chunk_text = models.TextField('Текст чанка')
    embedding = models.JSONField('Вектор')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Чанк статьи'
        verbose_name_plural = 'Чанки статей'
        unique_together = [('article', 'chunk_index')]


_CHUNK_SIZE = 800     # символов в одном чанке
_CHUNK_OVERLAP = 200  # перекрытие между чанками
_MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'


def _get_embed_model():
    from sentence_transformers import SentenceTransformer
    import torch as _torch
    device = 'cuda' if _torch.cuda.is_available() else 'cpu'
    try:
        return SentenceTransformer(_MODEL_NAME, device=device)
    except RuntimeError:
        return SentenceTransformer(_MODEL_NAME, device='cpu')


def _split_chunks(title: str, content: str) -> list[str]:
    """Разбить текст статьи на перекрывающиеся чанки."""
    # Заголовок добавляется к каждому чанку для контекста
    header = f'{title}\n\n'
    text = content.strip()
    if not text:
        return [header.strip()] if title else []

    # Если текст короткий — один чанк
    if len(text) <= _CHUNK_SIZE:
        return [f'{header}{text}']

    chunks = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_SIZE
        chunk = text[start:end]
        # Не обрывать на середине слова
        if end < len(text):
            last_space = chunk.rfind(' ')
            if last_space > _CHUNK_SIZE // 2:
                chunk = chunk[:last_space]
                end = start + last_space
        chunks.append(f'{header}{chunk.strip()}')
        start = end - _CHUNK_OVERLAP
        if start >= len(text):
            break
    return chunks


def index_wiki_article(article: WikiArticle) -> bool:
    """
    Разбить статью на чанки и вычислить эмбеддинг каждого.
    Медленно, но качественно: длинные статьи полностью покрываются.
    """
    if article.is_deleted:
        WikiArticleChunk.objects.filter(article=article).delete()
        return True

    chunks = _split_chunks(article.title, article.content)
    if not chunks:
        WikiArticleChunk.objects.filter(article=article).delete()
        return False

    try:
        model = _get_embed_model()
        # Кодируем все чанки разом (эффективнее по памяти, чем по одному)
        vectors = model.encode(chunks, convert_to_numpy=True, show_progress_bar=False)
    except Exception as e:
        logger.warning('Wiki chunk embedding failed for %s: %s', article.slug, e)
        return False

    # Атомарно заменяем все чанки статьи
    WikiArticleChunk.objects.filter(article=article).delete()
    objs = [
        WikiArticleChunk(
            article=article,
            chunk_index=i,
            chunk_text=chunk,
            embedding=vec.tolist(),
        )
        for i, (chunk, vec) in enumerate(zip(chunks, vectors))
    ]
    WikiArticleChunk.objects.bulk_create(objs)
    logger.info('Indexed %d chunks for article %s', len(objs), article.slug)
    return True


def wiki_semantic_search(query: str, space=None, article_ids=None, top_k: int = 5) -> list:
    """
    Семантический поиск по чанкам статей.
    article_ids — ограничить поиск списком pk (для кастомных ботов по разделу).
    Возвращает список dict: {article, score, excerpt} — по одному лучшему чанку на статью.
    Безопасен при большом числе статей: всё через numpy, без ORM N+1.
    """
    import numpy as np

    qs = WikiArticleChunk.objects.select_related('article').filter(
        article__is_deleted=False,
    )
    if space is not None:
        qs = qs.filter(article__space=space)
    if article_ids is not None:
        qs = qs.filter(article__pk__in=article_ids)

    rows = list(qs)
    if not rows:
        return []

    try:
        model = _get_embed_model()
        q_vec = model.encode(query, convert_to_numpy=True)
    except Exception as e:
        logger.warning('Wiki semantic search encode failed: %s', e)
        return []

    # Векторизованный cosine similarity через numpy (быстро, не падает)
    q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-9)
    matrix = np.array([row.embedding for row in rows], dtype='float32')
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    scores = matrix.dot(q_norm) / norms.squeeze()

    # По каждой статье берём лучший чанк
    best: dict[int, tuple] = {}  # article_pk → (score, chunk_text, article)
    for i, row in enumerate(rows):
        art = row.article
        sc = float(scores[i])
        if art.pk not in best or sc > best[art.pk][0]:
            best[art.pk] = (sc, row.chunk_text, art)

    sorted_best = sorted(best.values(), key=lambda x: -x[0])
    results = []
    for score, chunk_text, article in sorted_best[:top_k]:
        excerpt = chunk_text[:300].replace('\n', ' ').strip()
        results.append({'article': article, 'score': score, 'excerpt': excerpt})
    return results
