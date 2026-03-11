"""
Экстрактор сущностей для графа знаний.

Стратегия:
1. Люди  — из speaker_names + NER (spaCy PER) из транскрипции
2. Темы  — TF-IDF-like ключевые слова (существительные / именные группы)
3. Орги  — NER (spaCy ORG)
4. Решения и задачи — паттерны в тексте
5. Запись / Вики  — сами объекты становятся узлами-источниками
"""
from __future__ import annotations

import re
import logging
from collections import Counter

logger = logging.getLogger(__name__)

# ── NLP (spaCy, опционально) ──────────────────────────────────────────────────
_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            _nlp = spacy.load("ru_core_news_sm")
        except Exception:
            _nlp = False  # недоступен
    return _nlp if _nlp else None


# ── Паттерны решений и задач ─────────────────────────────────────────────────
_DECISION_RE = re.compile(
    r'(?:решил[иа]?|договорил[иа]?сь|согласовал[иа]?|утвердил[иа]?|принял[иа]? решение)[^.!?]{5,80}',
    re.IGNORECASE,
)
_TASK_RE = re.compile(
    r'(?:нужно|надо|задача|поручить|сделать|подготовить|разработать|проверить|отправить)[^.!?]{5,80}',
    re.IGNORECASE,
)

_STOP_WORDS = {
    'это', 'как', 'так', 'вот', 'всё', 'что', 'для', 'при', 'или', 'уже',
    'если', 'но', 'а', 'и', 'в', 'на', 'с', 'к', 'от', 'до', 'по', 'за',
    'не', 'да', 'же', 'бы', 'ли', 'они', 'мы', 'вы', 'он', 'она', 'его',
    'её', 'их', 'нас', 'вас', 'им', 'нам', 'вам', 'себя', 'себе',
    'этот', 'эта', 'эти', 'того', 'тем', 'том', 'были', 'было', 'будет',
    'есть', 'быть', 'очень', 'тоже', 'все', 'там', 'здесь', 'когда',
    'чтобы', 'потому', 'хотя', 'также', 'ещё', 'еще', 'только', 'через',
    'между', 'после', 'перед', 'более', 'менее', 'такой', 'такая', 'такие',
}


def _clean_text(text: str) -> str:
    """Убираем технические артефакты транскрипции."""
    text = re.sub(r'— Участник \d+:', '', text)
    text = re.sub(r'\[.*?\]', '', text)
    return text.strip()


def _extract_topics_regex(text: str, top_n: int = 15) -> list[str]:
    """Простая эвристика: самые частые слова длиннее 4 букв, не в стоп-листе."""
    words = re.findall(r'[А-Яа-яёЁ]{5,}', text)
    words = [w.lower() for w in words if w.lower() not in _STOP_WORDS]
    cnt = Counter(words)
    return [w for w, _ in cnt.most_common(top_n)]


def _extract_entities_spacy(text: str) -> dict[str, list[str]]:
    """NER через spaCy. Возвращает {'PER': [...], 'ORG': [...]}."""
    nlp = _get_nlp()
    if not nlp:
        return {'PER': [], 'ORG': []}
    doc = nlp(text[:50_000])  # ограничиваем размер
    persons = list({ent.text.strip() for ent in doc.ents if ent.label_ == 'PER' and len(ent.text.strip()) > 2})
    orgs = list({ent.text.strip() for ent in doc.ents if ent.label_ == 'ORG' and len(ent.text.strip()) > 2})
    return {'PER': persons, 'ORG': orgs}


def _get_or_create_node(space, node_type, title, recording=None, wiki_article=None, weight_inc=1.0):
    from .models import KGNode
    title = title.strip()[:299]
    if not title:
        return None
    node, created = KGNode.objects.get_or_create(
        space=space,
        node_type=node_type,
        title=title,
        defaults={'recording': recording, 'wiki_article': wiki_article, 'weight': weight_inc},
    )
    if not created:
        node.weight += weight_inc
        node.save(update_fields=['weight', 'updated_at'])
    return node


def _get_or_create_edge(source, target, relation_type, recording=None, wiki_article=None, weight_inc=1.0):
    from .models import KGEdge
    if source.pk == target.pk:
        return None
    edge, created = KGEdge.objects.get_or_create(
        source=source,
        target=target,
        relation_type=relation_type,
        recording=recording,
        wiki_article=wiki_article,
        defaults={'weight': weight_inc},
    )
    if not created:
        edge.weight += weight_inc
        edge.save(update_fields=['weight'])
    return edge


def extract_recording(recording) -> int:
    """
    Извлекает сущности из одной записи и сохраняет в граф.
    Возвращает количество созданных/обновлённых узлов.
    """
    from .models import KGNode
    space = recording.space
    text = recording.transcription or ''
    if not text.strip():
        return 0

    clean = _clean_text(text)
    count = 0

    # 1. Узел-запись
    rec_node = _get_or_create_node(
        space, KGNode.NodeType.RECORDING,
        recording.ai_title or recording.filename,
        recording=recording, weight_inc=1.0,
    )
    count += 1

    # 2. Спикеры из speaker_names
    speaker_nodes = []
    speaker_names = recording.speaker_names or {}
    for raw_key, name in speaker_names.items():
        if name and isinstance(name, str) and name.strip():
            node = _get_or_create_node(space, KGNode.NodeType.PERSON, name.strip(), weight_inc=2.0)
            if node:
                _get_or_create_edge(node, rec_node, 'mentioned_in', recording=recording)
                speaker_nodes.append(node)
                count += 1

    # 3. Люди из NER (spaCy)
    ents = _extract_entities_spacy(clean)
    for person_name in ents['PER']:
        # Пропускаем если уже есть из speaker_names
        if any(n.title.lower() == person_name.lower() for n in speaker_nodes):
            continue
        node = _get_or_create_node(space, KGNode.NodeType.PERSON, person_name, weight_inc=1.0)
        if node:
            _get_or_create_edge(node, rec_node, 'mentioned_in', recording=recording)
            count += 1

    # 4. Организации из NER
    for org_name in ents['ORG']:
        node = _get_or_create_node(space, KGNode.NodeType.ORGANIZATION, org_name, weight_inc=1.0)
        if node:
            _get_or_create_edge(node, rec_node, 'mentioned_in', recording=recording)
            count += 1

    # 5. Темы (ключевые слова)
    for topic in _extract_topics_regex(clean, top_n=10):
        node = _get_or_create_node(space, KGNode.NodeType.TOPIC, topic, weight_inc=1.0)
        if node:
            _get_or_create_edge(rec_node, node, 'speaks_about', recording=recording)
            # Связываем спикеров с темами
            for sp in speaker_nodes:
                _get_or_create_edge(sp, node, 'speaks_about', recording=recording)
            count += 1

    # 6. Решения
    for m in _DECISION_RE.finditer(clean):
        snippet = m.group(0).strip()[:200]
        node = _get_or_create_node(space, KGNode.NodeType.DECISION, snippet, recording=recording, weight_inc=1.0)
        if node:
            _get_or_create_edge(rec_node, node, 'decides', recording=recording)
            for sp in speaker_nodes:
                _get_or_create_edge(sp, node, 'decides', recording=recording)
            count += 1

    # 7. Задачи
    for m in _TASK_RE.finditer(clean):
        snippet = m.group(0).strip()[:200]
        node = _get_or_create_node(space, KGNode.NodeType.TASK, snippet, recording=recording, weight_inc=1.0)
        if node:
            _get_or_create_edge(rec_node, node, 'assigns_task', recording=recording)
            count += 1

    logger.info('KG: запись %s — %d узлов', recording.pk, count)
    return count


def extract_wiki(article) -> int:
    """Извлекает сущности из статьи вики."""
    from .models import KGNode
    import re as _re
    space = article.space
    # Убираем Markdown разметку
    text = _re.sub(r'[#*`\[\]_~]', '', article.content or '')
    text = _re.sub(r'```.*?```', '', text, flags=_re.DOTALL)
    if not text.strip():
        return 0

    count = 0

    # Узел-статья
    wiki_node = _get_or_create_node(
        space, KGNode.NodeType.WIKI, article.title,
        wiki_article=article, weight_inc=1.0,
    )
    count += 1

    ents = _extract_entities_spacy(text)
    for person_name in ents['PER']:
        node = _get_or_create_node(space, KGNode.NodeType.PERSON, person_name, weight_inc=1.0)
        if node:
            _get_or_create_edge(node, wiki_node, 'mentioned_in', wiki_article=article)
            count += 1

    for org_name in ents['ORG']:
        node = _get_or_create_node(space, KGNode.NodeType.ORGANIZATION, org_name, weight_inc=1.0)
        if node:
            _get_or_create_edge(node, wiki_node, 'mentioned_in', wiki_article=article)
            count += 1

    for topic in _extract_topics_regex(text, top_n=8):
        node = _get_or_create_node(space, KGNode.NodeType.TOPIC, topic, weight_inc=1.0)
        if node:
            _get_or_create_edge(wiki_node, node, 'related_to', wiki_article=article)
            count += 1

    return count
