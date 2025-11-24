"""
Сценарные тесты для views
"""
import pytest
import json
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from transcribe.models import Transcription


@pytest.mark.django_db
class TestIndexView:
    """Тесты главной страницы"""
    
    def test_index_page_loads(self, client):
        """Тест загрузки главной страницы"""
        response = client.get('/')
        assert response.status_code == 200
        assert 'transcriptions' in response.context
    
    def test_index_with_password_phrase(self, client):
        """Тест главной страницы с фразой-паролем"""
        # Создаем транскрипцию с паролем
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("secret")
        )
        
        # Без входа транскрипция не видна
        response = client.get('/')
        assert transcription not in response.context['transcriptions']
        
        # Входим по фразе-паролю
        session = client.session
        session['password_phrase'] = 'secret'
        session.save()
        
        response = client.get('/')
        assert transcription in response.context['transcriptions']


@pytest.mark.django_db
class TestUploadFile:
    """Тесты загрузки файлов"""
    
    def test_upload_file_no_file(self, client):
        """Тест загрузки без файла"""
        response = client.post('/upload/')
        assert response.status_code == 400
        data = json.loads(response.content)
        assert 'error' in data
    
    def test_upload_file_success(self, client):
        """Тест успешной загрузки файла"""
        # Создаем тестовый файл
        audio_file = SimpleUploadedFile(
            "test.mp3",
            b"fake audio content" * 100,
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': audio_file,
            'whisper_model': 'base'
        })
        
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['success'] is True
        assert 'transcription_ids' in data
        
        # Проверяем, что транскрипция создана
        transcription_id = data['transcription_ids'][0]
        transcription = Transcription.objects.get(id=transcription_id)
        assert transcription.filename == "test.mp3"
        assert transcription.status == "pending"
    
    def test_upload_file_with_password_phrase(self, client):
        """Тест загрузки файла с фразой-паролем"""
        audio_file = SimpleUploadedFile(
            "test.mp3",
            b"fake audio content" * 100,
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': audio_file,
            'password_phrase': 'secret',
            'whisper_model': 'base'
        })
        
        assert response.status_code == 200
        data = json.loads(response.content)
        transcription_id = data['transcription_ids'][0]
        transcription = Transcription.objects.get(id=transcription_id)
        assert transcription.password_phrase_hash is not None
        assert transcription.check_password_phrase('secret') is True
    
    def test_upload_file_too_large(self, client):
        """Тест загрузки слишком большого файла"""
        # Создаем файл больше 500 МБ
        large_file = SimpleUploadedFile(
            "large.mp3",
            b"x" * (501 * 1024 * 1024),  # 501 МБ
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': large_file,
            'whisper_model': 'base'
        })
        
        assert response.status_code == 400
        data = json.loads(response.content)
        assert 'error' in data
        assert 'большой' in data['error'].lower() or 'large' in data['error'].lower()


@pytest.mark.django_db
class TestLoginLogout:
    """Тесты входа и выхода"""
    
    def test_login_with_phrase(self, client):
        """Тест входа по фразе-паролю"""
        response = client.post('/login/', {
            'password_phrase': 'secret'
        })
        
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['success'] is True
        
        # Проверяем сессию
        session = client.session
        assert session.get('password_phrase') == 'secret'
    
    def test_login_empty_phrase(self, client):
        """Тест входа с пустой фразой"""
        response = client.post('/login/', {
            'password_phrase': ''
        })
        
        assert response.status_code == 400
        data = json.loads(response.content)
        assert 'error' in data
    
    def test_logout(self, client):
        """Тест выхода"""
        # Сначала входим
        session = client.session
        session['password_phrase'] = 'secret'
        session.save()
        
        # Выходим
        response = client.post('/logout/')
        
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['success'] is True
        
        # Проверяем, что сессия очищена
        session = client.session
        assert session.get('password_phrase') is None


@pytest.mark.django_db
class TestTranscriptionDetail:
    """Тесты детальной страницы транскрипции"""
    
    def test_transcription_detail_not_found(self, client):
        """Тест детальной страницы несуществующей транскрипции"""
        response = client.get('/transcription/99999/')
        assert response.status_code == 404
    
    def test_transcription_detail_access_denied(self, client):
        """Тест доступа к защищенной транскрипции"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("secret"),
            transcribed_text="Test transcription"
        )
        
        response = client.get(f'/transcription/{transcription.id}/')
        assert response.status_code == 403
    
    def test_transcription_detail_with_password(self, client):
        """Тест доступа к защищенной транскрипции с паролем"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("secret"),
            transcribed_text="Test transcription",
            status="completed"
        )
        
        # Входим по паролю
        session = client.session
        session['password_phrase'] = 'secret'
        session.save()
        
        response = client.get(f'/transcription/{transcription.id}/')
        assert response.status_code == 200
        assert transcription.filename in response.content.decode()


@pytest.mark.django_db
class TestTranscriptionStatus:
    """Тесты статуса транскрипции"""
    
    def test_transcription_status_pending(self, client):
        """Тест статуса ожидающей транскрипции"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            status="pending"
        )
        
        response = client.get(f'/transcription/{transcription.id}/status/')
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['status'] == 'pending'
        assert data['text'] is None
    
    def test_transcription_status_completed(self, client):
        """Тест статуса завершенной транскрипции"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            status="completed",
            transcribed_text="Test transcription text"
        )
        
        response = client.get(f'/transcription/{transcription.id}/status/')
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['status'] == 'completed'
        assert data['text'] == "Test transcription text"


@pytest.mark.django_db
class TestDownload:
    """Тесты скачивания"""
    
    def test_download_text(self, client):
        """Тест скачивания текста"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            status="completed",
            transcribed_text="Test transcription text"
        )
        
        response = client.get(f'/transcription/{transcription.id}/download-text/')
        assert response.status_code == 200
        assert response['Content-Type'] == 'text/plain; charset=utf-8'
        assert b"Test transcription text" in response.content
    
    def test_download_text_access_denied(self, client):
        """Тест скачивания защищенного текста без доступа"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("secret"),
            transcribed_text="Test transcription text"
        )
        
        response = client.get(f'/transcription/{transcription.id}/download-text/')
        assert response.status_code == 403


