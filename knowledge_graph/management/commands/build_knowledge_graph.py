"""python manage.py build_knowledge_graph [--space org-bp] [--clear]"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Строит граф знаний из транскрипций и статей вики'

    def add_arguments(self, parser):
        parser.add_argument('--space', default=None, help='Slug пространства (по умолчанию — все)')
        parser.add_argument('--clear', action='store_true', help='Очистить граф перед построением')

    def handle(self, *args, **options):
        from recordings.models import Recording, Space
        from wiki_kb.models import WikiArticle
        from knowledge_graph.models import KGNode, KGEdge
        from knowledge_graph.extractor import extract_recording, extract_wiki

        space_slug = options.get('space')
        do_clear = options.get('clear')

        spaces = Space.objects.all()
        if space_slug:
            spaces = spaces.filter(slug=space_slug)

        if do_clear:
            if space_slug:
                KGEdge.objects.filter(source__space__slug=space_slug).delete()
                KGNode.objects.filter(space__slug=space_slug).delete()
            else:
                KGEdge.objects.all().delete()
                KGNode.objects.all().delete()
            self.stdout.write('Граф очищен.')

        total_nodes = 0

        for space in spaces:
            self.stdout.write(f'Пространство: {space.name} ({space.slug})')

            recs = Recording.objects.filter(
                space=space,
            ).exclude(transcription='').exclude(transcription__isnull=True)
            self.stdout.write(f'  Записей с транскрипцией: {recs.count()}')

            for rec in recs:
                try:
                    n = extract_recording(rec)
                    total_nodes += n
                    self.stdout.write(f'  ✓ запись {rec.pk}: +{n} узлов')
                except Exception as e:
                    self.stderr.write(f'  ✗ запись {rec.pk}: {e}')

            articles = WikiArticle.objects.filter(space=space, is_deleted=False)
            self.stdout.write(f'  Статей вики: {articles.count()}')

            for art in articles:
                try:
                    n = extract_wiki(art)
                    total_nodes += n
                except Exception as e:
                    self.stderr.write(f'  ✗ статья {art.pk}: {e}')

        self.stdout.write(self.style.SUCCESS(
            f'\nГотово. Всего обработано узлов: {total_nodes}'
        ))
        self.stdout.write(f'Узлов в БД: {KGNode.objects.count()}, Связей: {KGEdge.objects.count()}')
