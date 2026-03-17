import time
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from recordings.services import run_poll_and_transcribe, process_one_transcribe, process_one_embedding, check_meeting_notifications

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Опрос S3 каждые N сек, обработка очередей транскрибации и эмбеддингов.'

    def handle(self, *args, **options):
        interval = settings.POLL_INTERVAL_SECONDS
        self._reset_stuck_transcriptions()
        self.stdout.write(f'Опрос S3 каждые {interval} сек. Очереди: транскрибация, эмбеддинг. Остановка: Ctrl+C.')
        while True:
            # Опрос S3 + сброс зависших
            try:
                run_poll_and_transcribe()
            except Exception as e:
                logger.exception('Poller cycle failed: %s', e)

            try:
                check_meeting_notifications()
            except Exception as e:
                logger.exception('Meeting notifications failed: %s', e)

            # Полностью дренируем очередь транскрибации — без сна между задачами
            while True:
                try:
                    had_work = process_one_transcribe()
                except Exception as e:
                    logger.exception('Transcribe task failed: %s', e)
                    had_work = False
                if not had_work:
                    break

            # Полностью дренируем очередь эмбеддингов
            while True:
                try:
                    had_work = process_one_embedding()
                except Exception as e:
                    logger.exception('Embedding task failed: %s', e)
                    had_work = False
                if not had_work:
                    break

            # Спим только когда обе очереди пусты — до следующего опроса S3
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                break

        self.stdout.write('Poller остановлен.')

    def _reset_stuck_transcriptions(self):
        """При старте сбрасываем записи застрявшие в 'transcribing' → failed (без повторной постановки в очередь)."""
        from recordings.models import Recording
        stuck = Recording.objects.filter(status=Recording.Status.TRANSCRIBING)
        count = stuck.count()
        if count:
            stuck.update(
                status=Recording.Status.FAILED,
                transcription_progress=0,
                transcription_stage='',
                error_message='Прервано: сервер был перезапущен',
            )
            self.stdout.write(f'Recovery: сброшено {count} зависших транскрибаций → failed')
