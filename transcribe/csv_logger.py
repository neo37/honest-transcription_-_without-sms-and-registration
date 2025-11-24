"""
Утилита для логирования IP и UUID в CSV файл
"""
import csv
import os
from datetime import datetime
from django.conf import settings
import threading

# Блокировка для потокобезопасной записи в CSV
csv_lock = threading.Lock()

CSV_FILE_PATH = os.path.join(settings.BASE_DIR, 'uploads_log.csv')


def ensure_csv_file():
    """Создает CSV файл с заголовками если его нет"""
    if not os.path.exists(CSV_FILE_PATH):
        with open(CSV_FILE_PATH, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'ip_address', 'uuid', 'filename', 'file_size'])


def log_upload(ip_address, uuid, filename=None, file_size=None):
    """
    Логирует загрузку в CSV файл
    
    Args:
        ip_address: IP адрес клиента
        uuid: UUID пользователя
        filename: Имя файла (опционально)
        file_size: Размер файла (опционально)
    """
    ensure_csv_file()
    
    with csv_lock:
        try:
            with open(CSV_FILE_PATH, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().isoformat(),
                    ip_address,
                    uuid,
                    filename or '',
                    file_size or ''
                ])
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка при записи в CSV: {e}")

