import time
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from recordings.services import run_poll_and_transcribe, process_one_transcribe, process_one_embedding

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Опрос S3 каждые N сек, обработка очередей транскрибации и эмбеддингов.'

    def handle(self, *args, **options):
        interval = settings.POLL_INTERVAL_SECONDS
        self.stdout.write(f'Опрос S3 каждые {interval} сек. Очереди: транскрибация, эмбеддинг. Остановка: Ctrl+C.')
        while True:
            try:
                run_poll_and_transcribe()
            except Exception as e:
                logger.exception('Poller cycle failed: %s', e)
            for _ in range(3):
                try:
                    if not process_one_transcribe():
                        break
                except Exception as e:
                    logger.exception('Transcribe task failed: %s', e)
            for _ in range(5):
                try:
                    if not process_one_embedding():
                        break
                except Exception as e:
                    logger.exception('Embedding task failed: %s', e)
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                break
        self.stdout.write('Poller остановлен.')
