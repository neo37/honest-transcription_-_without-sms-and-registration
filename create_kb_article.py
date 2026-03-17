#!/usr/bin/env python
"""
One-shot script: create KB implementation plan wiki article.
Run inside container: python /app/create_kb_article.py
"""
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meetrec.settings')
django.setup()

from wiki_kb.models import WikiArticle, index_wiki_article

SLUG = 'kb-implementation-plan'
TITLE = 'База знаний: план внедрения (BP + CPQ + встречи)'

CONTENT = r'''
# База знаний: план внедрения

Документ описывает архитектуру и поэтапный план создания единой семантической базы знаний, объединяющей:

- Вики-статьи экосистемы **BusinessPad** и **CPQ**
- Транскрипты **встреч** (неструктурированный текст)
- Возможность **диалогового поиска** через LLM (Gonka API)

---

## Текущее состояние инфраструктуры

| Компонент | Статус |
|---|---|
| PostgreSQL + pgvector | Готов (wiki_kb использует JSON-векторы) |
| Sentence Transformers `paraphrase-multilingual-MiniLM-L12-v2` | Готов |
| WikiArticleChunk + `index_wiki_article()` | Готов |
| `wiki_semantic_search()` | Готов |
| Gonka API (LLM) | Готов (SystemConfig `gonka_api_key`) |
| Транскрипция с диаризацией | Готов (WhisperX / faster-whisper + FoxNose/pyannote) |

Технический долг минимален — инфраструктура уже есть, нужно её расширить.

---

## Фаза 1 — Единый индекс (1–2 недели)

### 1.1 Добавить pgvector как первичное хранилище векторов

Текущий `WikiArticleChunk.embedding` — `JSONField`. При росте базы (>10 000 чанков) поиск через numpy замедлится.

```python
# wiki_kb/models.py — заменить JSONField на pgvector
from pgvector.django import VectorField

class WikiArticleChunk(models.Model):
    embedding = VectorField(dimensions=384, null=True)  # MiniLM-L12-v2 = 384d
```

```sql
-- миграция: создать HNSW-индекс
CREATE INDEX wiki_chunk_emb_hnsw ON wiki_kb_wikiarticlechunk
    USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
```

### 1.2 Добавить источники в индекс

Добавить модель `KBSource` — универсальный чанк из любого источника:

```python
class KBChunk(models.Model):
    SOURCE_WIKI = 'wiki'
    SOURCE_TRANSCRIPT = 'transcript'
    SOURCE_EXTERNAL = 'external'
    SOURCE_CHOICES = [(SOURCE_WIKI, 'Вики'), (SOURCE_TRANSCRIPT, 'Транскрипт'), (SOURCE_EXTERNAL, 'Внешний')]

    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    source_id   = models.CharField(max_length=128)   # slug статьи / pk записи
    source_title = models.CharField(max_length=512)
    chunk_index = models.SmallIntegerField(default=0)
    chunk_text  = models.TextField()
    embedding   = VectorField(dimensions=384, null=True)
    space       = models.ForeignKey('recordings.Space', null=True, on_delete=models.SET_NULL)
    updated_at  = models.DateTimeField(auto_now=True)
```

### 1.3 Транскрипты → чанки

После каждой успешной транскрипции вызывать `index_transcript(recording)`:

```python
def index_transcript(recording):
    text = recording.transcription or ''
    if not text.strip():
        return
    chunks = _split_chunks(recording.title or str(recording.pk), text)
    model = _get_embed_model()
    vectors = model.encode(chunks, convert_to_numpy=True)
    KBChunk.objects.filter(source_type='transcript', source_id=str(recording.pk)).delete()
    KBChunk.objects.bulk_create([
        KBChunk(source_type='transcript', source_id=str(recording.pk),
                source_title=recording.title or '', chunk_index=i,
                chunk_text=c, embedding=v.tolist(), space=recording.space)
        for i, (c, v) in enumerate(zip(chunks, vectors))
    ])
```

Вызов — в конце `transcribe_one()`:

```python
from wiki_kb.models import index_transcript
try:
    index_transcript(rec)
    _step(attempt_steps, 'embedding', engine='sentence-transformers',
          model='paraphrase-multilingual-MiniLM-L12-v2', device='cpu')
except Exception as e:
    logger.warning('Embedding failed for rec %s: %s', rec.pk, e)
```

### 1.4 Внешние источники (BP Wiki, CPQ Wiki)

Для индексации внешних Confluence/Notion-страниц — периодическая задача:

```python
# management/commands/sync_external_kb.py
class Command(BaseCommand):
    def handle(self, *args, **options):
        for source in ExternalKBSource.objects.filter(enabled=True):
            pages = fetch_pages(source)   # HTTP/REST к Confluence/Notion
            for page in pages:
                index_external_page(page, source)
```

**Альтернатива без API**: загрузка экспорта (HTML/Markdown) → парсинг → индексация.

---

## Фаза 2 — RAG-диалог (1 неделя)

### 2.1 Retrieval

```python
def kb_search(query: str, space=None, top_k=8) -> list[KBChunk]:
    model = _get_embed_model()
    q_vec = model.encode(query)
    # pgvector cosine distance
    qs = KBChunk.objects.order_by(
        CosineDistance('embedding', q_vec)
    ).select_related()
    if space:
        qs = qs.filter(space=space)
    return list(qs[:top_k])
```

### 2.2 Augmentation + Generation (Gonka API)

```python
def kb_answer(question: str, space=None) -> dict:
    chunks = kb_search(question, space=space, top_k=8)
    context = '\n\n'.join(
        f'[{c.source_title}]\n{c.chunk_text}' for c in chunks
    )
    prompt = (
        'Ты ассистент команды. Отвечай на русском, опираясь на контекст.\n\n'
        'Контекст:\n' + context + '\n\nВопрос: ' + question + '\nОтвет:'
    )
    response = gonka_complete(prompt)   # вызов Gonka API
    return {'answer': response, 'sources': chunks}
```

### 2.3 UI — чат в разделе /kb/

Добавить страницу `/kb/ask/`:

- Текстовое поле вопроса
- Список источников (ссылки на статьи/записи)
- Стриминговый ответ через SSE или fetch

---

## Фаза 3 — Граф знаний (опционально, 2–3 недели)

Автоматически извлекать сущности и связи из транскриптов и вики:

```
Транскрипт → spaCy NER → сущности (продукты, проекты, люди)
           → relation extraction → тройки (Субъект, Отношение, Объект)
           → Neo4j / simple FK-таблица → GraphQL API
```

Пример тройки из встречи:
- `(BusinessPad, интегрирован_с, CPQ)`
- `(Иван Иванов, отвечает_за, Модуль котировок)`

Граф позволяет отвечать на структурные вопросы: *«Кто работает над CPQ?»*, *«Какие модули связаны с тарификацией?»*

---

## Фаза 4 — Качество и мониторинг

- **Relevance feedback**: кнопка 👍/👎 у каждого ответа → дообучение или prompt-тюнинг
- **Chunk quality**: логировать топ-K чанков и оценку cosine similarity для каждого запроса
- **Re-indexing pipeline**: при правке вики-статьи автоматически пересчитывать чанки (signal `post_save`)
- **Дедупликация**: при индексации внешних источников — near-duplicate detection через cosine > 0.97

---

## Стек итоговый

| Слой | Технология |
|---|---|
| Эмбеддинги | `paraphrase-multilingual-MiniLM-L12-v2` (384d) |
| Хранилище векторов | PostgreSQL + pgvector + HNSW |
| Транскрипция | WhisperX / faster-whisper + FoxNose diarize |
| LLM (генерация) | Gonka API |
| Поиск | pgvector cosine distance |
| UI | Django + vanilla JS (SSE стриминг) |
| Внешние источники | REST API Confluence / экспорт Markdown |

---

## Приоритеты

1. **Сразу**: Фаза 1.3 — индексация транскриптов (данные уже есть)
2. **Неделя 1**: Фаза 1.1 — миграция на pgvector + HNSW
3. **Неделя 2**: Фаза 2 — RAG-диалог через Gonka
4. **Далее**: Фаза 1.4 (внешние источники) → Фаза 3 (граф)
'''.strip()

article, created = WikiArticle.objects.get_or_create(
    slug=SLUG,
    defaults=dict(
        title=TITLE,
        content=CONTENT,
        parent=None,
    )
)

if not created:
    article.title = TITLE
    article.content = CONTENT
    article.save()
    print(f'Updated article: {SLUG}')
else:
    print(f'Created article: {SLUG}')

print('Indexing chunks...')
ok = index_wiki_article(article)
print(f'Indexing {"OK" if ok else "FAILED (no sentence-transformers?)"}: {article.pk} / {article.slug}')
