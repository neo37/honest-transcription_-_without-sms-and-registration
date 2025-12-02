"""
Логирование в Elasticsearch для Kibana
"""
import logging
import json
from datetime import datetime
from django.conf import settings
import requests

logger = logging.getLogger(__name__)

# Настройки Elasticsearch из settings
ELASTICSEARCH_URL = getattr(settings, 'ELASTICSEARCH_URL', 'http://logs-1.business-pad.com:9200')
ELASTICSEARCH_INDEX = getattr(settings, 'ELASTICSEARCH_INDEX', 'whisper-transcribe')
ELASTICSEARCH_ENABLED = getattr(settings, 'ELASTICSEARCH_ENABLED', True)


def log_to_elasticsearch(event_type, data, level='info'):
    """
    Логирует событие в Elasticsearch
    
    Args:
        event_type: Тип события (upload, transcription_start, transcription_complete, error, etc.)
        data: Словарь с данными для логирования
        level: Уровень логирования (info, warning, error)
    """
    if not ELASTICSEARCH_ENABLED:
        return
    
    try:
        # Формируем документ для Elasticsearch
        document = {
            '@timestamp': datetime.utcnow().isoformat() + 'Z',
            'event_type': event_type,
            'level': level,
            'service': 'whisper-transcribe',
            **data
        }
        
        # URL для индексации документа
        url = f"{ELASTICSEARCH_URL}/{ELASTICSEARCH_INDEX}/_doc"
        
        # Отправляем запрос в Elasticsearch
        response = requests.post(
            url,
            json=document,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
        
        if response.status_code not in [200, 201]:
            logger.warning(f"Не удалось отправить лог в Elasticsearch: {response.status_code} - {response.text}")
    
    except requests.exceptions.RequestException as e:
        # Не логируем ошибки подключения к Elasticsearch в основной лог, чтобы не засорять
        pass
    except Exception as e:
        logger.error(f"Ошибка при логировании в Elasticsearch: {e}", exc_info=True)

