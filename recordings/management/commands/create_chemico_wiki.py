"""
Management-команда: создать/обновить Wiki-статью со схемой БД Chemico.

Использование:
  python manage.py create_chemico_wiki

Создаёт статью /kb/chemico-db-schema/ с описанием таблиц.
Редактируйте её в Wiki для улучшения ответов агента.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Создать/обновить Wiki-статью со схемой БД Chemico для AI-агента'

    def handle(self, *args, **options):
        from chemico_agent.knowledge import ensure_wiki_article
        url = ensure_wiki_article()
        self.stdout.write(self.style.SUCCESS(f'Wiki-статья готова: {url}'))
        self.stdout.write('')
        self.stdout.write('Настройки агента в Django Admin → SystemConfig:')
        self.stdout.write('  chemico_use_wiki    = true/false  (использовать Wiki)')
        self.stdout.write('  chemico_llm_provider = openai/anthropic/grok/gonka')
        self.stdout.write('  chemico_llm_model    = gpt-4o / claude-sonnet-4-6 / ...')
        self.stdout.write('')
        self.stdout.write('Команды бота:')
        self.stdout.write('  /db <вопрос>  — задать вопрос по БД')
        self.stdout.write('  /db_model     — показать текущий провайдер')
        self.stdout.write('  /db_help      — справка')
