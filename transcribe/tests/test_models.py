"""
Тесты для моделей
"""
import pytest
from django.test import TestCase
from django.utils import timezone
from transcribe.models import Transcription, Screenshot


@pytest.mark.django_db
class TestTranscriptionModel:
    """Тесты модели Transcription"""
    
    def test_create_transcription(self):
        """Тест создания транскрипции"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            status="pending"
        )
        assert transcription.id is not None
        assert transcription.filename == "test.mp3"
        assert transcription.status == "pending"
    
    def test_password_phrase_hashing(self):
        """Тест хеширования фразы-пароля"""
        phrase = "test_password"
        hash1 = Transcription.hash_password_phrase(phrase)
        hash2 = Transcription.hash_password_phrase(phrase)
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest length
        assert hash1 != phrase
    
    def test_check_password_phrase(self):
        """Тест проверки фразы-пароля"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("secret")
        )
        
        assert transcription.check_password_phrase("secret") is True
        assert transcription.check_password_phrase("wrong") is False
        assert transcription.check_password_phrase(None) is False
    
    def test_generate_public_token(self):
        """Тест генерации публичного токена"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024
        )
        
        token = transcription.generate_public_token()
        assert token is not None
        assert len(token) == 32
        assert transcription.public_token == token
        
        # Повторный вызов должен вернуть тот же токен
        token2 = transcription.generate_public_token()
        assert token2 == token


@pytest.mark.django_db
class TestScreenshotModel:
    """Тесты модели Screenshot"""
    
    def test_create_screenshot(self):
        """Тест создания скриншота"""
        transcription = Transcription.objects.create(
            filename="test.mp4",
            ip_address="127.0.0.1",
            file_size=1024
        )
        
        screenshot = Screenshot.objects.create(
            transcription=transcription,
            timestamp=60.0,
            image_path="screenshots/1/screenshot_0001.jpg",
            order=1
        )
        
        assert screenshot.id is not None
        assert screenshot.transcription == transcription
        assert screenshot.timestamp == 60.0
        assert screenshot.order == 1


