from django.db import models


class KGNode(models.Model):
    """Узел графа знаний."""

    class NodeType(models.TextChoices):
        PERSON = 'person', 'Человек'
        TOPIC = 'topic', 'Тема'
        ORGANIZATION = 'org', 'Организация'
        DECISION = 'decision', 'Решение'
        TASK = 'task', 'Задача'
        RECORDING = 'recording', 'Запись'
        WIKI = 'wiki', 'Статья вики'

    space = models.ForeignKey(
        'recordings.Space', null=True, blank=True,
        on_delete=models.CASCADE, related_name='kg_nodes',
    )
    node_type = models.CharField(max_length=20, choices=NodeType.choices)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default='')
    # Ссылки на источники
    recording = models.ForeignKey(
        'recordings.Recording', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kg_nodes',
    )
    wiki_article = models.ForeignKey(
        'wiki_kb.WikiArticle', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kg_nodes',
    )
    weight = models.FloatField(default=1.0, help_text='Частота / важность')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('space', 'node_type', 'title')
        verbose_name = 'Узел графа'
        verbose_name_plural = 'Узлы графа'
        ordering = ['-weight', 'title']

    def __str__(self):
        return f'[{self.node_type}] {self.title}'


class KGEdge(models.Model):
    """Связь между узлами графа."""

    class RelationType(models.TextChoices):
        MENTIONED_IN = 'mentioned_in', 'Упомянут в'
        SPEAKS_ABOUT = 'speaks_about', 'Говорит о'
        DECIDES = 'decides', 'Принимает решение'
        ASSIGNS_TASK = 'assigns_task', 'Назначает задачу'
        RELATED_TO = 'related_to', 'Связан с'
        PART_OF = 'part_of', 'Часть'

    source = models.ForeignKey(KGNode, on_delete=models.CASCADE, related_name='outgoing_edges')
    target = models.ForeignKey(KGNode, on_delete=models.CASCADE, related_name='incoming_edges')
    relation_type = models.CharField(max_length=30, choices=RelationType.choices, default='related_to')
    weight = models.FloatField(default=1.0, help_text='Сила связи / частота совместного упоминания')
    # Источник связи
    recording = models.ForeignKey(
        'recordings.Recording', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kg_edges',
    )
    wiki_article = models.ForeignKey(
        'wiki_kb.WikiArticle', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kg_edges',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('source', 'target', 'relation_type', 'recording', 'wiki_article')
        verbose_name = 'Связь графа'
        verbose_name_plural = 'Связи графа'

    def __str__(self):
        return f'{self.source} → [{self.relation_type}] → {self.target}'
