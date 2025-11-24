"""
Интеграционные сценарные тесты
"""
import pytest
import json
from django.test import Client
from transcribe.models import Transcription


@pytest.mark.integration
@pytest.mark.django_db
class TestFullWorkflow:
    """Полный сценарий работы приложения"""
    
    def test_full_upload_and_view_workflow(self, client):
        """Полный сценарий: загрузка -> просмотр -> скачивание"""
        # 1. Загружаем файл
        from django.core.files.uploadedfile import SimpleUploadedFile
        audio_file = SimpleUploadedFile(
            "test.mp3",
            b"fake audio content" * 100,
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': audio_file,
            'whisper_model': 'base',
            'signature': 'Test signature'
        })
        
        assert response.status_code == 200
        data = json.loads(response.content)
        transcription_id = data['transcription_ids'][0]
        
        # 2. Проверяем статус
        response = client.get(f'/transcription/{transcription_id}/status/')
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['status'] in ['pending', 'processing', 'completed']
        
        # 3. Создаем транскрипцию вручную для теста (так как обработка асинхронная)
        transcription = Transcription.objects.get(id=transcription_id)
        transcription.status = 'completed'
        transcription.transcribed_text = "Test transcription text"
        transcription.save()
        
        # 4. Просматриваем детали
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 200
        
        # 5. Скачиваем текст
        response = client.get(f'/transcription/{transcription_id}/download-text/')
        assert response.status_code == 200
        assert b"Test transcription text" in response.content
    
    def test_password_protected_workflow(self, client):
        """Сценарий работы с защищенными паролем транскрипциями"""
        # 1. Загружаем файл с паролем
        from django.core.files.uploadedfile import SimpleUploadedFile
        audio_file = SimpleUploadedFile(
            "secret.mp3",
            b"fake audio content" * 100,
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': audio_file,
            'password_phrase': 'secret123',
            'whisper_model': 'base'
        })
        
        assert response.status_code == 200
        data = json.loads(response.content)
        transcription_id = data['transcription_ids'][0]
        
        transcription = Transcription.objects.get(id=transcription_id)
        transcription.status = 'completed'
        transcription.transcribed_text = "Secret transcription"
        transcription.save()
        
        # 2. Пытаемся получить доступ без пароля
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 403
        
        # 3. Входим по паролю
        response = client.post('/login/', {
            'password_phrase': 'secret123'
        })
        assert response.status_code == 200
        
        # 4. Теперь доступ открыт
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 200
        
        # 5. Выходим
        response = client.post('/logout/')
        assert response.status_code == 200
        
        # 6. Доступ снова закрыт
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 403
    
    def test_multiple_files_upload(self, client):
        """Сценарий загрузки нескольких файлов"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        file1 = SimpleUploadedFile("test1.mp3", b"content1" * 100, content_type="audio/mpeg")
        file2 = SimpleUploadedFile("test2.mp3", b"content2" * 100, content_type="audio/mpeg")
        
        response = client.post('/upload/', {
            'file': [file1, file2],
            'whisper_model': 'base'
        })
        
        assert response.status_code == 200
        data = json.loads(response.content)
        assert len(data['transcription_ids']) == 2
        assert data['count'] == 2
        
        # Проверяем, что у них одинаковая сессия
        transcription1 = Transcription.objects.get(id=data['transcription_ids'][0])
        transcription2 = Transcription.objects.get(id=data['transcription_ids'][1])
        assert transcription1.upload_session == transcription2.upload_session


