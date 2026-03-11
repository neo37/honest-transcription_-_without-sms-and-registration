import threading
from django.apps import AppConfig


class WikiKbConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'wiki_kb'
    verbose_name = 'База знаний'

    def ready(self):
        from django.db.models.signals import post_save
        from .models import WikiArticle

        def _on_article_save(sender, instance, **kwargs):
            if instance.is_deleted:
                return
            # Индексируем в фоновом потоке чтобы не блокировать запрос
            def _do():
                from .models import index_wiki_article
                index_wiki_article(instance)
            threading.Thread(target=_do, daemon=True).start()

        post_save.connect(_on_article_save, sender=WikiArticle, weak=False)
