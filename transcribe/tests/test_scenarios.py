"""
Сценарные тесты с прокликиванием интерфейса
"""
import pytest
import time
from django.test import Client, LiveServerTestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from transcribe.models import Transcription


@pytest.mark.e2e
@pytest.mark.django_db
class TestFullUserScenarios:
    """Полные сценарии использования приложения"""
    
    def test_scenario_1_upload_and_view(self, client):
        """Сценарий 1: Загрузка файла и просмотр результата"""
        # 1. Открываем главную страницу
        response = client.get('/')
        assert response.status_code == 200
        
        # 2. Загружаем файл
        audio_file = SimpleUploadedFile(
            "test_scenario_1.mp3",
            b"fake audio content" * 100,
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': audio_file,
            'whisper_model': 'base',
            'signature': 'Test Scenario 1'
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        transcription_id = data['transcription_ids'][0]
        
        # 3. Создаем транскрипцию вручную для теста
        transcription = Transcription.objects.get(id=transcription_id)
        transcription.status = 'completed'
        transcription.transcribed_text = "Это тестовая транскрипция для сценария 1"
        transcription.save()
        
        # 4. Проверяем статус
        response = client.get(f'/transcription/{transcription_id}/status/')
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'completed'
        
        # 5. Просматриваем детали
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 200
        assert "test_scenario_1.mp3" in response.content.decode()
        
        # 6. Скачиваем текст
        response = client.get(f'/transcription/{transcription_id}/download-text/')
        assert response.status_code == 200
        assert b"Это тестовая транскрипция" in response.content
    
    def test_scenario_2_password_protection(self, client):
        """Сценарий 2: Защита паролем и доступ"""
        # 1. Загружаем файл с паролем
        audio_file = SimpleUploadedFile(
            "secret_scenario_2.mp3",
            b"secret content" * 100,
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': audio_file,
            'password_phrase': 'secret123',
            'whisper_model': 'base'
        })
        
        assert response.status_code == 200
        data = response.json()
        transcription_id = data['transcription_ids'][0]
        
        transcription = Transcription.objects.get(id=transcription_id)
        transcription.status = 'completed'
        transcription.transcribed_text = "Секретная транскрипция"
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
        
        # 5. Проверяем главную страницу - видим транскрипцию
        response = client.get('/')
        assert response.status_code == 200
        transcriptions = response.context['transcriptions']
        assert transcription in transcriptions
        
        # 6. Выходим
        response = client.post('/logout/')
        assert response.status_code == 200
        
        # 7. Доступ снова закрыт
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 403
    
    def test_scenario_3_multiple_files(self, client):
        """Сценарий 3: Загрузка нескольких файлов"""
        # 1. Загружаем несколько файлов
        file1 = SimpleUploadedFile("file1.mp3", b"content1" * 100, content_type="audio/mpeg")
        file2 = SimpleUploadedFile("file2.mp3", b"content2" * 100, content_type="audio/mpeg")
        file3 = SimpleUploadedFile("file3.mp3", b"content3" * 100, content_type="audio/mpeg")
        
        response = client.post('/upload/', {
            'file': [file1, file2, file3],
            'whisper_model': 'base',
            'signature': 'Multiple files test'
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data['count'] == 3
        assert len(data['transcription_ids']) == 3
        
        # 2. Проверяем, что у них одинаковая сессия
        transcription1 = Transcription.objects.get(id=data['transcription_ids'][0])
        transcription2 = Transcription.objects.get(id=data['transcription_ids'][1])
        transcription3 = Transcription.objects.get(id=data['transcription_ids'][2])
        
        assert transcription1.upload_session == transcription2.upload_session
        assert transcription2.upload_session == transcription3.upload_session
        
        # 3. Завершаем транскрипции
        for t_id in data['transcription_ids']:
            t = Transcription.objects.get(id=t_id)
            t.status = 'completed'
            t.transcribed_text = f"Транскрипция файла {t.filename}"
            t.save()
        
        # 4. Скачиваем текст всей сессии
        response = client.get(f'/session/{transcription1.upload_session}/download-text/')
        assert response.status_code == 200
        content = response.content.decode()
        assert "file1.mp3" in content
        assert "file2.mp3" in content
        assert "file3.mp3" in content
    
    def test_scenario_4_file_too_large(self, client):
        """Сценарий 4: Попытка загрузить слишком большой файл"""
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
        data = response.json()
        assert 'error' in data
        assert 'большой' in data['error'].lower() or 'large' in data['error'].lower()
    
    def test_scenario_5_empty_file(self, client):
        """Сценарий 5: Попытка загрузить пустой файл"""
        empty_file = SimpleUploadedFile(
            "empty.mp3",
            b"",
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': empty_file,
            'whisper_model': 'base'
        })
        
        # Файл должен быть отклонен или обработан с ошибкой
        # В зависимости от реализации
        assert response.status_code in [200, 400]
    
    def test_scenario_6_public_link_with_password(self, client):
        """Сценарий 6: Публичная ссылка с паролем"""
        # 1. Создаем транскрипцию с паролем
        transcription = Transcription.objects.create(
            filename="public_test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("public_secret"),
            status="completed",
            transcribed_text="Публичная транскрипция"
        )
        transcription.generate_public_token()
        
        # 2. Пытаемся получить доступ без токена пароля
        response = client.get(f'/public/{transcription.public_token}/')
        assert response.status_code == 403
        
        # 3. Генерируем правильный токен пароля
        import hashlib
        password_token = hashlib.sha256(
            f"{transcription.public_token}_{transcription.password_phrase_hash}".encode()
        ).hexdigest()[:16]
        
        # 4. Получаем доступ с токеном
        response = client.get(f'/public/{transcription.public_token}/?p={password_token}')
        assert response.status_code == 200
        assert "public_test.mp3" in response.content.decode()
    
    def test_scenario_7_login_logout_cycle(self, client):
        """Сценарий 7: Множественные входы и выходы"""
        # Создаем транскрипции с разными паролями
        t1 = Transcription.objects.create(
            filename="test1.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("pass1")
        )
        
        t2 = Transcription.objects.create(
            filename="test2.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("pass2")
        )
        
        # 1. Входим с паролем 1
        response = client.post('/login/', {'password_phrase': 'pass1'})
        assert response.status_code == 200
        
        response = client.get('/')
        transcriptions = response.context['transcriptions']
        assert t1 in transcriptions
        assert t2 not in transcriptions
        
        # 2. Выходим
        response = client.post('/logout/')
        assert response.status_code == 200
        
        # 3. Входим с паролем 2
        response = client.post('/login/', {'password_phrase': 'pass2'})
        assert response.status_code == 200
        
        response = client.get('/')
        transcriptions = response.context['transcriptions']
        assert t1 not in transcriptions
        assert t2 in transcriptions
    
    def test_scenario_8_error_handling(self, client):
        """Сценарий 8: Обработка ошибок"""
        # 1. Попытка получить несуществующую транскрипцию
        response = client.get('/transcription/99999/')
        assert response.status_code == 404
        
        # 2. Попытка получить статус несуществующей транскрипции
        response = client.get('/transcription/99999/status/')
        assert response.status_code == 404
        
        # 3. Попытка скачать несуществующую транскрипцию
        response = client.get('/transcription/99999/download-text/')
        assert response.status_code == 404
        
        # 4. Загрузка без файла
        response = client.post('/upload/')
        assert response.status_code == 400
        
        # 5. Вход с пустым паролем
        response = client.post('/login/', {'password_phrase': ''})
        assert response.status_code == 400
    
    def test_scenario_9_different_whisper_models(self, client):
        """Сценарий 9: Тестирование разных моделей Whisper"""
        models = ['tiny', 'base', 'small']
        
        for model in models:
            audio_file = SimpleUploadedFile(
                f"test_{model}.mp3",
                b"content" * 100,
                content_type="audio/mpeg"
            )
            
            response = client.post('/upload/', {
                'file': audio_file,
                'whisper_model': model
            })
            
            assert response.status_code == 200
            data = response.json()
            transcription = Transcription.objects.get(id=data['transcription_ids'][0])
            assert transcription.whisper_model == model
    
    def test_scenario_10_screenshot_extraction(self, client):
        """Сценарий 10: Извлечение скриншотов (если видео)"""
        # Создаем транскрипцию с флагом извлечения скриншотов
        audio_file = SimpleUploadedFile(
            "test_video.mp4",
            b"fake video content" * 100,
            content_type="video/mp4"
        )
        
        response = client.post('/upload/', {
            'file': audio_file,
            'extract_screenshots': 'on',
            'whisper_model': 'base'
        })
        
        assert response.status_code == 200
        data = response.json()
        transcription = Transcription.objects.get(id=data['transcription_ids'][0])
        assert transcription.extract_screenshots is True


