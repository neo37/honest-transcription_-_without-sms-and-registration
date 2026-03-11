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
        self._reset_stuck_transcriptions()
        self.stdout.write(f'Опрос S3 каждые {interval} сек. Очереди: транскрибация, эмбеддинг. Остановка: Ctrl+C.')
        while True:
            try:
                run_poll_and_transcribe()
            except Exception as e:
                logger.exception('Poller cycle failed: %s', e)
            try:
                process_one_transcribe()
            except Exception as e:
                logger.exception('Transcribe task failed: %s', e)
            try:
                process_one_embedding()
            except Exception as e:
                logger.exception('Embedding task failed: %s', e)
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                break
        self.stdout.write('Poller остановлен.')

    def _reset_stuck_transcriptions(self):
        """При старте сбрасываем записи застрявшие в 'transcribing' — они зависли из-за краша."""
        from recordings.models import Recording
        stuck = Recording.objects.filter(status=Recording.Status.TRANSCRIBING)
        count = stuck.count()
        if count:
            stuck.update(status=Recording.Status.PENDING, transcription_progress=0, transcription_stage='')
            self.stdout.write(f'Recovery: сброшено {count} зависших транскрибаций → pending')
