"""
E2E сценарные тесты с полным прокликиванием
"""
import pytest
import time
from django.test import Client
from django.core.files.uploadedfile import SimpleUploadedFile
from transcribe.models import Transcription


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.django_db
class TestE2EScenarios:
    """End-to-end сценарии с полным взаимодействием"""
    
    def test_complete_workflow_scenario(self, client):
        """Полный сценарий: от загрузки до скачивания"""
        # Шаг 1: Открываем главную страницу
        response = client.get('/')
        assert response.status_code == 200
        assert 'transcriptions' in response.context
        
        # Шаг 2: Загружаем файл с подписью
        audio_file = SimpleUploadedFile(
            "complete_workflow.mp3",
            b"complete workflow test content" * 200,
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': audio_file,
            'whisper_model': 'base',
            'signature': 'Complete Workflow Test',
            'extract_screenshots': 'off'
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        transcription_id = data['transcription_ids'][0]
        upload_session = data['upload_session']
        
        # Шаг 3: Проверяем статус (pending)
        response = client.get(f'/transcription/{transcription_id}/status/')
        assert response.status_code == 200
        status_data = response.json()
        assert status_data['status'] == 'pending'
        
        # Шаг 4: Симулируем завершение транскрипции
        transcription = Transcription.objects.get(id=transcription_id)
        transcription.status = 'processing'
        transcription.save()
        
        response = client.get(f'/transcription/{transcription_id}/status/')
        assert response.json()['status'] == 'processing'
        
        transcription.status = 'completed'
        transcription.transcribed_text = "Это полная транскрипция для теста workflow. Она содержит несколько предложений для проверки."
        transcription.save()
        
        # Шаг 5: Проверяем статус (completed)
        response = client.get(f'/transcription/{transcription_id}/status/')
        status_data = response.json()
        assert status_data['status'] == 'completed'
        assert status_data['text'] is not None
        
        # Шаг 6: Просматриваем детали
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 200
        content = response.content.decode()
        assert "complete_workflow.mp3" in content
        
        # Шаг 7: Скачиваем текст
        response = client.get(f'/transcription/{transcription_id}/download-text/')
        assert response.status_code == 200
        assert "Это полная транскрипция".encode('utf-8') in response.content
        
        # Шаг 8: Проверяем главную страницу - видим транскрипцию
        response = client.get('/')
        transcriptions = response.context['transcriptions']
        assert transcription in transcriptions
    
    def test_password_protection_full_scenario(self, client):
        """Полный сценарий защиты паролем"""
        # Шаг 1: Загружаем файл с паролем
        audio_file = SimpleUploadedFile(
            "protected.mp3",
            b"protected content" * 100,
            content_type="audio/mpeg"
        )
        
        response = client.post('/upload/', {
            'file': audio_file,
            'password_phrase': 'my_secret_password',
            'whisper_model': 'base'
        })
        
        assert response.status_code == 200
        data = response.json()
        transcription_id = data['transcription_ids'][0]
        
        transcription = Transcription.objects.get(id=transcription_id)
        transcription.status = 'completed'
        transcription.transcribed_text = "Защищенная транскрипция"
        transcription.save()
        
        # Шаг 2: Пытаемся получить доступ без пароля
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 403
        
        response = client.get(f'/transcription/{transcription_id}/download-text/')
        assert response.status_code == 403
        
        # Шаг 3: Входим по паролю
        response = client.post('/login/', {
            'password_phrase': 'my_secret_password'
        })
        assert response.status_code == 200
        
        # Шаг 4: Теперь доступ открыт
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 200
        
        response = client.get(f'/transcription/{transcription_id}/download-text/')
        assert response.status_code == 200
        
        # Шаг 5: Проверяем главную страницу
        response = client.get('/')
        transcriptions = response.context['transcriptions']
        assert transcription in transcriptions
        assert response.context['is_logged_in'] is True
        
        # Шаг 6: Выходим
        response = client.post('/logout/')
        assert response.status_code == 200
        
        # Шаг 7: Доступ снова закрыт
        response = client.get(f'/transcription/{transcription_id}/')
        assert response.status_code == 403
        
        response = client.get('/')
        assert response.context['is_logged_in'] is False
    
    def test_multiple_files_session_scenario(self, client):
        """Сценарий работы с сессией из нескольких файлов"""
        # Шаг 1: Загружаем несколько файлов
        files = [
            SimpleUploadedFile(f"session_file_{i}.mp3", b"content" * 100, content_type="audio/mpeg")
            for i in range(1, 4)
        ]
        
        response = client.post('/upload/', {
            'file': files,
            'whisper_model': 'base',
            'signature': 'Session Test'
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data['count'] == 3
        
        transcription_ids = data['transcription_ids']
        upload_session = data['upload_session']
        
        # Шаг 2: Завершаем все транскрипции
        for i, t_id in enumerate(transcription_ids, 1):
            t = Transcription.objects.get(id=t_id)
            t.status = 'completed'
            t.transcribed_text = f"Транскрипция файла {i}: {t.filename}"
            t.save()
        
        # Шаг 3: Проверяем детали каждого файла
        for t_id in transcription_ids:
            response = client.get(f'/transcription/{t_id}/')
            assert response.status_code == 200
        
        # Шаг 4: Скачиваем текст всей сессии
        response = client.get(f'/session/{upload_session}/download-text/')
        assert response.status_code == 200
        content = response.content.decode()
        
        for i in range(1, 4):
            assert f"session_file_{i}.mp3" in content
    
    def test_error_recovery_scenario(self, client):
        """Сценарий обработки ошибок и восстановления"""
        # Шаг 1: Создаем транскрипцию с ошибкой
        transcription = Transcription.objects.create(
            filename="error_test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            status="error",
            error_message="Тестовая ошибка обработки"
        )
        
        # Шаг 2: Проверяем статус ошибки
        response = client.get(f'/transcription/{transcription.id}/status/')
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'error'
        assert data['error'] is not None
        
        # Шаг 3: Просматриваем детали (должна быть видна ошибка)
        response = client.get(f'/transcription/{transcription.id}/')
        assert response.status_code == 200
        content = response.content.decode()
        assert "error" in content.lower() or "ошибка" in content.lower()
        
        # Шаг 4: Исправляем ошибку (симуляция)
        transcription.status = 'pending'
        transcription.error_message = None
        transcription.save()
        
        # Шаг 5: Проверяем, что статус изменился
        response = client.get(f'/transcription/{transcription.id}/status/')
        assert response.json()['status'] == 'pending'
    
    def test_public_link_scenario(self, client):
        """Сценарий работы с публичными ссылками"""
        # Шаг 1: Создаем транскрипцию с паролем
        transcription = Transcription.objects.create(
            filename="public_link_test.mp3",
            ip_address="127.0.0.1",
            file_size=1024,
            password_phrase_hash=Transcription.hash_password_phrase("public_pass"),
            status="completed",
            transcribed_text="Публичная транскрипция для теста"
        )
        transcription.generate_public_token()
        
        # Шаг 2: Пытаемся получить доступ без токена
        response = client.get(f'/public/{transcription.public_token}/')
        assert response.status_code == 403
        
        # Шаг 3: Генерируем правильный токен
        import hashlib
        password_token = hashlib.sha256(
            f"{transcription.public_token}_{transcription.password_phrase_hash}".encode()
        ).hexdigest()[:16]
        
        # Шаг 4: Получаем доступ с токеном
        response = client.get(f'/public/{transcription.public_token}/?p={password_token}')
        assert response.status_code == 200
        content = response.content.decode()
        assert "public_link_test.mp3" in content
        
        # Шаг 5: Скачиваем текст через публичную ссылку
        response = client.get(f'/public/{transcription.public_token}/download-text/?p={password_token}')
        assert response.status_code == 200
        assert "Публичная транскрипция".encode('utf-8') in response.content

