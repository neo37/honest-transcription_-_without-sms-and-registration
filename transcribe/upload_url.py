import requests
import os
import tempfile
import re
from urllib.parse import urlparse, parse_qs
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


def download_from_url(url, timeout=1800):
    """Скачивает файл по URL и возвращает путь к временному файлу"""
    try:
        # Обработка cloud.mail.ru
        if 'cloud.mail.ru' in url:
            return download_from_cloud_mail_ru(url, timeout)
        
        # Обычная загрузка по URL
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, stream=True, timeout=timeout)
        response.raise_for_status()
        
        # Определяем имя файла
        filename = None
        if 'Content-Disposition' in response.headers:
            content_disposition = response.headers['Content-Disposition']
            filename_match = re.search(r'filename="?([^"]+)"?', content_disposition)
            if filename_match:
                filename = filename_match.group(1)
        
        if not filename:
            # Пытаемся извлечь имя из URL
            parsed_url = urlparse(url)
            filename = os.path.basename(parsed_url.path)
            if not filename or filename == '/':
                filename = 'downloaded_file'
        
        # Создаем временный файл
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1])
        temp_file_path = temp_file.name
        
        # Скачиваем файл порциями
        total_size = 0
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                temp_file.write(chunk)
                total_size += len(chunk)
        
        temp_file.close()
        
        logger.info(f"Файл скачан с URL: {url}, размер: {total_size} байт, путь: {temp_file_path}")
        return temp_file_path, filename
        
    except Exception as e:
        logger.error(f"Ошибка при скачивании файла с URL {url}: {e}", exc_info=True)
        raise Exception(f"Не удалось скачать файл: {str(e)}")


def download_from_cloud_mail_ru(url, timeout=1800):
    """Скачивает файл из публичной папки cloud.mail.ru"""
    try:
        # Парсим URL cloud.mail.ru
        # Формат: https://cloud.mail.ru/public/C6tJ/QNx88M4S3?autologin=no
        parsed_url = urlparse(url)
        path_parts = parsed_url.path.strip('/').split('/')
        
        if len(path_parts) < 3 or path_parts[0] != 'public':
            raise Exception("Неверный формат ссылки cloud.mail.ru")
        
        folder_hash = path_parts[1]
        file_hash = path_parts[2]
        
        # Получаем информацию о файле через API cloud.mail.ru
        api_url = f"https://cloud.mail.ru/api/v2/folder?weblink={file_hash}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Пробуем получить прямую ссылку на скачивание
        # Для публичных ссылок cloud.mail.ru используем прямой доступ
        download_url = f"https://cloud.mail.ru/public/{folder_hash}/{file_hash}"
        
        # Пробуем скачать через публичную ссылку
        response = requests.get(download_url, headers=headers, stream=True, timeout=timeout, allow_redirects=True)
        
        # Если получили редирект или ошибку, пробуем другой способ
        if response.status_code != 200:
            # Пробуем через API для получения прямой ссылки
            try:
                api_response = requests.get(api_url, headers=headers, timeout=30)
                if api_response.status_code == 200:
                    data = api_response.json()
                    if 'body' in data and 'weblink' in data['body']:
                        weblink_data = data['body']['weblink']
                        if 'url' in weblink_data:
                            download_url = weblink_data['url']
                            response = requests.get(download_url, headers=headers, stream=True, timeout=timeout)
            except:
                pass
        
        response.raise_for_status()
        
        # Определяем имя файла
        filename = None
        if 'Content-Disposition' in response.headers:
            content_disposition = response.headers['Content-Disposition']
            filename_match = re.search(r'filename="?([^"]+)"?', content_disposition)
            if filename_match:
                filename = filename_match.group(1)
        
        if not filename:
            # Пытаемся извлечь из URL или используем хеш
            filename = f"cloud_mail_ru_{file_hash}"
        
        # Создаем временный файл
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1] if '.' in filename else '')
        temp_file_path = temp_file.name
        
        # Скачиваем файл порциями
        total_size = 0
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                temp_file.write(chunk)
                total_size += len(chunk)
        
        temp_file.close()
        
        logger.info(f"Файл скачан с cloud.mail.ru: {url}, размер: {total_size} байт, путь: {temp_file_path}")
        return temp_file_path, filename
        
    except Exception as e:
        logger.error(f"Ошибка при скачивании файла с cloud.mail.ru {url}: {e}", exc_info=True)
        raise Exception(f"Не удалось скачать файл с cloud.mail.ru: {str(e)}")

