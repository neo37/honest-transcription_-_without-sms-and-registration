import uuid
from django.db import models


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
