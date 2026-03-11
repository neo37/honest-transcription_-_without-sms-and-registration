import threading
from django.contrib import admin
from django.contrib import messages
from django.utils.html import format_html
from .models import WikiArticle, WikiArticleEmbedding, WikiArticleChunk, WikiRevision


@admin.register(WikiArticle)
class WikiArticleAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'space', 'parent', 'embedding_status', 'updated_at')
    list_filter = ('space', 'is_deleted')
    search_fields = ('title', 'slug', 'content')
    raw_id_fields = ('parent', 'space', 'created_by', 'updated_by')
    actions = ['reindex_embeddings']

    def embedding_status(self, obj):
        chunks = list(WikiArticleChunk.objects.filter(article=obj))
        if not chunks:
            return format_html('<span style="color:#d97706">⚪ нет</span>')
        total_chars = sum(len(c.chunk_text) for c in chunks)
        dims = len(chunks[0].embedding) if chunks else 0
        return format_html(
            '<span style="color:#16a34a">✅ {} чанков · {}d · {} симв.</span>',
            len(chunks), dims, total_chars,
        )
    embedding_status.short_description = 'Эмбеддинг'

    @admin.action(description='Переиндексировать эмбеддинги (чанки, фоново)')
    def reindex_embeddings(self, request, queryset):
        articles = list(queryset.filter(is_deleted=False))

        def _do():
            from .models import index_wiki_article
            for art in articles:
                index_wiki_article(art)

        threading.Thread(target=_do, daemon=True).start()
        self.message_user(
            request,
            f'Запущена индексация {len(articles)} статей в фоне.',
            messages.SUCCESS,
        )


@admin.register(WikiArticleChunk)
class WikiArticleChunkAdmin(admin.ModelAdmin):
    list_display = ('article', 'chunk_index', 'chunk_preview', 'dims', 'updated_at')
    list_filter = ('article__space',)
    search_fields = ('article__title', 'chunk_text')
    raw_id_fields = ('article',)
    readonly_fields = ('updated_at', 'dims')

    def chunk_preview(self, obj):
        return obj.chunk_text[:80] + '…' if len(obj.chunk_text) > 80 else obj.chunk_text
    chunk_preview.short_description = 'Текст чанка'

    def dims(self, obj):
        return len(obj.embedding) if obj.embedding else 0
    dims.short_description = 'Размерность'


@admin.register(WikiArticleEmbedding)
class WikiArticleEmbeddingAdmin(admin.ModelAdmin):
    list_display = ('article', 'dims', 'updated_at')
    raw_id_fields = ('article',)
    readonly_fields = ('updated_at',)

    def dims(self, obj):
        return len(obj.embedding) if obj.embedding else 0
    dims.short_description = 'Размерность'


@admin.register(WikiRevision)
class WikiRevisionAdmin(admin.ModelAdmin):
    list_display = ('article', 'revised_by', 'comment', 'created_at', 'content_preview')
    list_filter = ('article__space',)
    search_fields = ('article__title', 'revised_by__email', 'comment')
    raw_id_fields = ('article', 'revised_by')
    readonly_fields = ('created_at',)

    def content_preview(self, obj):
        return (obj.content[:80] + '…') if len(obj.content) > 80 else obj.content
    content_preview.short_description = 'Текст (превью)'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
