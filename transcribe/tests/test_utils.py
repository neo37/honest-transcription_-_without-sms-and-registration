"""
Тесты для утилит
"""
import pytest
from django.test import RequestFactory
from transcribe.utils import (
    get_client_ip,
    check_transcription_access,
    validate_file_size,
    validate_whisper_model,
    split_text_into_slides
)
from transcribe.models import Transcription


@pytest.mark.django_db
class TestUtils:
    """Тесты утилит"""
    
    def test_get_client_ip(self):
        """Тест получения IP адреса"""
        factory = RequestFactory()
        request = factory.get('/')
        request.META['REMOTE_ADDR'] = '192.168.1.1'
        
        ip = get_client_ip(request)
        assert ip == '192.168.1.1'
    
    def test_get_client_ip_with_proxy(self):
        """Тест получения IP через прокси"""
        factory = RequestFactory()
        request = factory.get('/')
        request.META['HTTP_X_FORWARDED_FOR'] = '192.168.1.1, 10.0.0.1'
        
        ip = get_client_ip(request)
        assert ip == '192.168.1.1'
    
    def test_check_transcription_access_no_password(self):
        """Тест проверки доступа без пароля"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024
        )
        
        factory = RequestFactory()
        request = factory.get('/')
        request.session = {}
        
        has_access, is_public, error = check_transcription_access(transcription, request)
        assert has_access is True
        assert is_public is False
        assert error is None
    
    def test_check_transcription_access_with_password(self):
        """Тест проверки доступа с паролем"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("secret")
        )
        
        factory = RequestFactory()
        request = factory.get('/')
        request.session = {'password_phrase': 'secret'}
        
        has_access, is_public, error = check_transcription_access(transcription, request)
        assert has_access is True
    
    def test_check_transcription_access_wrong_password(self):
        """Тест проверки доступа с неверным паролем"""
        transcription = Transcription.objects.create(
            filename="test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("secret")
        )
        
        factory = RequestFactory()
        request = factory.get('/')
        request.session = {'password_phrase': 'wrong'}
        
        has_access, is_public, error = check_transcription_access(transcription, request)
        assert has_access is False
        assert error is not None
    
    def test_validate_file_size(self):
        """Тест валидации размера файла"""
        # Валидный размер
        is_valid, error = validate_file_size(100 * 1024 * 1024)  # 100 МБ
        assert is_valid is True
        assert error is None
        
        # Слишком большой файл
        is_valid, error = validate_file_size(600 * 1024 * 1024)  # 600 МБ
        assert is_valid is False
        assert error is not None
        
        # Пустой файл
        is_valid, error = validate_file_size(0)
        assert is_valid is False
        assert error is not None
    
    def test_validate_whisper_model(self):
        """Тест валидации модели Whisper"""
        model, is_valid = validate_whisper_model('base')
        assert model == 'base'
        assert is_valid is True
        
        model, is_valid = validate_whisper_model('invalid')
        assert model == 'base'
        assert is_valid is False
    
    def test_split_text_into_slides(self):
        """Тест разбиения текста на слайды"""
        text = "Первое предложение. Второе предложение! Третье предложение?"
        slides = split_text_into_slides(text, max_chars=50)
        
        assert len(slides) > 0
        assert all(isinstance(slide, str) for slide in slides)
        
        # Тест с пустым текстом
        slides = split_text_into_slides("")
        assert len(slides) == 0
        
        # Тест с None
        slides = split_text_into_slides(None)
        assert len(slides) == 0


