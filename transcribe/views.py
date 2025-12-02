import os
import threading
import subprocess
import logging
import uuid
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from .models import Transcription, IPUploadCount, UUIDUploadCount
from .csv_logger import log_upload
from .utils import get_client_ip, validate_file_size, validate_whisper_model
from faster_whisper import WhisperModel
import tempfile
import shutil

logger = logging.getLogger(__name__)

# Импорт логирования в Elasticsearch (с обработкой ошибок импорта)
try:
    from .elastic_logger import log_to_elasticsearch
except ImportError:
    def log_to_elasticsearch(*args, **kwargs):
        pass  # Заглушка если модуль не доступен

# Импорт логирования в Elasticsearch (с обработкой ошибок импорта)
try:
    from .elastic_logger import log_to_elasticsearch
except ImportError:
    def log_to_elasticsearch(*args, **kwargs):
        pass  # Заглушка если модуль не доступен


# Кэш моделей Whisper (загружаются по требованию)
whisper_models_cache = {}
model_lock = threading.Lock()


def get_whisper_model(model_name='base'):
    """Получить модель Whisper (загружается по требованию, кэшируется)"""
    import logging
    logger = logging.getLogger(__name__)
    
    global whisper_models_cache
    
    if model_name not in whisper_models_cache:
        with model_lock:
            if model_name not in whisper_models_cache:
                logger.info(f"Загрузка модели Whisper: {model_name}...")
                try:
                    # Модели загружаются автоматически при первом использовании
                    # Они кэшируются в ~/.cache/huggingface/hub/
                    whisper_models_cache[model_name] = WhisperModel(
                        model_name, 
                        device="cpu", 
                        compute_type="int8",
                        download_root=None  # Использует стандартный кэш
                    )
                    logger.info(f"Модель Whisper {model_name} успешно загружена и кэширована")
                except Exception as e:
                    logger.error(f"Ошибка загрузки модели {model_name}: {e}", exc_info=True)
                    # Fallback на base если модель не загрузилась
                    if model_name != 'base':
                        logger.info("Используем модель base как fallback")
                        try:
                            whisper_models_cache[model_name] = get_whisper_model('base')
                        except:
                            raise Exception(f"Не удалось загрузить модель {model_name} и fallback base также не работает")
                    else:
                        raise Exception(f"Критическая ошибка: не удалось загрузить базовую модель Whisper: {e}")
    
    return whisper_models_cache[model_name]


def index(request):
    """Главная страница с формой загрузки и списком транскрипций"""
    import shutil
    
    # Получаем информацию о диске
    total, used, free = shutil.disk_usage('/')
    used_percent = (used / total) * 100
    disk_info = {
        'total_gb': total / (1024**3),
        'used_gb': used / (1024**3),
        'free_gb': free / (1024**3),
        'used_percent': used_percent,
        'used_percent_int': int(used_percent)  # Для использования в CSS
    }
    
    # Проверяем, есть ли активная фраза-пароль в сессии
    active_password_phrase = request.session.get('password_phrase', None)
    
    if active_password_phrase:
        # Фильтруем транскрипции по фразе-паролю
        password_hash = Transcription.hash_password_phrase(active_password_phrase)
        transcriptions = Transcription.objects.filter(
            password_phrase_hash=password_hash
        ).order_by('-uploaded_at').distinct()[:50]
    else:
        # Показываем только транскрипции без пароля
        # Показываем только последние 2 без пароля, остальные скрыты
        all_no_password = Transcription.objects.filter(
            password_phrase_hash__isnull=True
        ).order_by('-uploaded_at').distinct()
        
        # Берем последние 2 для отображения
        transcriptions = all_no_password[:2]
        
        # Удаляем старые файлы без пароля (оставляем только последние 2)
        # Файлы уже удалены после обработки, но транскрипции остаются в БД
    
    # Получаем баланс пользователя (если есть UUID в localStorage, он будет передан через JS)
    # Здесь мы не можем получить UUID из запроса, так как это GET запрос
    # Баланс будет получен через отдельный endpoint или через JS
    balance = None
    
    return render(request, 'transcribe/index.html', {
        'transcriptions': transcriptions,
        'is_logged_in': active_password_phrase is not None,
        'active_phrase': active_password_phrase if active_password_phrase else '',
        'disk_info': disk_info,
        'balance': balance
    })


@require_http_methods(["POST"])
def upload_file(request):
    """Обработка загрузки файлов (поддерживает множественную загрузку)"""
    if 'file' not in request.FILES:
        return JsonResponse({'error': 'Файл не найден'}, status=400)
    
    # Получаем все загруженные файлы
    uploaded_files = request.FILES.getlist('file')
    
    if not uploaded_files:
        return JsonResponse({'error': 'Файлы не найдены'}, status=400)
    
    # Получаем IP адрес
    from .utils import get_client_ip
    ip_address = get_client_ip(request)
    
    # Получаем UUID из запроса (должен быть передан с клиента)
    user_uuid = request.POST.get('user_uuid', '').strip()
    if not user_uuid:
        return JsonResponse({'error': 'UUID не передан'}, status=400)
    
    # Проверяем количество загрузок по IP за месяц
    ip_counter = IPUploadCount.get_or_create_for_ip(ip_address)
    ip_monthly_count = ip_counter.get_monthly_count()
    
    # Проверяем количество загрузок по UUID за месяц
    uuid_counter = UUIDUploadCount.get_or_create_for_uuid(user_uuid)
    uuid_monthly_count = uuid_counter.get_monthly_count()
    
    # Проверяем баланс - если баланс 0, требуется оплата
    ip_balance = ip_counter.balance if ip_counter else 0
    uuid_balance = uuid_counter.balance if uuid_counter else 0
    has_balance = ip_balance > 0 or uuid_balance > 0
    
    # Если требуется оплата (после 2-й загрузки за месяц по IP или UUID) И баланс 0
    requires_payment = False
    if ip_monthly_count >= 2 and not ip_counter.is_paid and not has_balance:
        requires_payment = True
    elif uuid_monthly_count >= 2 and not uuid_counter.is_paid and not has_balance:
        requires_payment = True
    
    if requires_payment:
        return JsonResponse({
            'error': 'Для продолжения использования сервиса требуется оплата 12 рублей. Пожалуйста, произведите оплату.',
            'requires_payment': True,
            'ip_count': ip_monthly_count,
            'uuid_count': uuid_monthly_count,
            'ip_balance': ip_balance,
            'uuid_balance': uuid_balance
        }, status=402)  # 402 Payment Required
    
    # Если баланс 0, но оплата не требуется (первые 2 загрузки), разрешаем загрузку
    
    # Получаем подпись (если есть)
    signature = request.POST.get('signature', '').strip()
    
    # Получаем фразу-пароль (если есть)
    password_phrase = request.POST.get('password_phrase', '').strip()
    password_phrase_hash = None
    if password_phrase:
        password_phrase_hash = Transcription.hash_password_phrase(password_phrase)
    
    # Получаем флаг извлечения скриншотов
    extract_screenshots = request.POST.get('extract_screenshots') == 'on'
    
    # Получаем и валидируем выбранную модель Whisper
    whisper_model, _ = validate_whisper_model(request.POST.get('whisper_model', 'base'))
    
    # Создаем уникальную сессию загрузки для группировки файлов
    upload_session = str(uuid.uuid4())
    
    # Проверка размера файлов (без ограничений - убрано ограничение 500 МБ)
    transcription_ids = []
    
    for uploaded_file in uploaded_files:
        # Минимальная валидация - файл не должен быть пустым
        if uploaded_file.size == 0:
            return JsonResponse({'error': f'Файл {uploaded_file.name} пустой'}, status=400)
        
        # Сохраняем файл в постоянное хранилище для возможности перетранскрибации
        # Создаем директорию для файла
        uploads_base_dir = os.path.join(settings.MEDIA_ROOT, 'uploads')
        os.makedirs(uploads_base_dir, exist_ok=True)
        
        # Создаем уникальную директорию для этого файла
        file_uuid = str(uuid.uuid4())
        uploads_dir = os.path.join(uploads_base_dir, file_uuid)
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Сохраняем оригинальный файл
        file_ext = os.path.splitext(uploaded_file.name)[1]
        original_filename = f"original{file_ext}"
        original_file_path = os.path.join(uploads_dir, original_filename)
        
        try:
            # ВАЖНО: Django InMemoryUploadedFile может быть уже прочитан
            # Сохраняем файл в постоянное хранилище
            uploaded_file.seek(0)  # Сбрасываем позицию на начало
            with open(original_file_path, 'wb') as f:
                # Читаем файл порциями для больших файлов
                for chunk in uploaded_file.chunks():
                    f.write(chunk)
            
            # Проверяем, что файл действительно сохранен
            if not os.path.exists(original_file_path):
                raise Exception(f"Файл не был создан: {original_file_path}")
            
            saved_size = os.path.getsize(original_file_path)
            if saved_size == 0:
                raise Exception(f"Файл пустой после сохранения: {original_file_path}")
            
            if saved_size != uploaded_file.size:
                logger.warning(f"Размер сохраненного файла ({saved_size}) не совпадает с оригинальным ({uploaded_file.size})")
            
            logger.info(f"Оригинальный файл сохранен: {original_file_path}, размер: {saved_size} байт")
        except Exception as e:
            logger.error(f"Ошибка при сохранении файла {uploaded_file.name}: {e}", exc_info=True)
            # Удаляем директорию если файл не сохранился
            try:
                if os.path.exists(uploads_dir):
                    shutil.rmtree(uploads_dir)
            except:
                pass
            return JsonResponse({'error': f'Ошибка при сохранении файла {uploaded_file.name}: {str(e)}'}, status=500)
        
        # Создаем запись в БД
        transcription = Transcription.objects.create(
            filename=uploaded_file.name,
            ip_address=ip_address,
            user_uuid=user_uuid,
            signature=signature if signature else None,
            password_phrase_hash=password_phrase_hash,
            file_size=uploaded_file.size,
            extract_screenshots=extract_screenshots,
            upload_session=upload_session,
            whisper_model=whisper_model,
            status='pending',
            original_file_path=original_file_path  # Сохраняем путь к оригинальному файлу
        )
        
        # Генерируем публичный токен сразу
        transcription.generate_public_token()
        
        # Увеличиваем счетчики загрузок
        ip_counter.increment_upload()
        uuid_counter.increment_upload()
        
        # Логируем в CSV
        log_upload(ip_address, user_uuid, uploaded_file.name, uploaded_file.size)
        
        # Логируем в Elasticsearch
        log_to_elasticsearch('file_upload', {
            'transcription_id': transcription.id,
            'filename': uploaded_file.name,
            'file_size': uploaded_file.size,
            'ip_address': ip_address,
            'user_uuid': user_uuid,
            'whisper_model': whisper_model,
            'has_password': bool(password_phrase_hash),
            'extract_screenshots': extract_screenshots
        })
        
        transcription_ids.append(transcription.id)
        
        # Запускаем обработку в отдельном потоке
        thread = threading.Thread(target=process_file, args=(transcription.id, original_file_path))
        thread.daemon = True
        thread.start()
    
    # Собираем информацию о загруженных файлах для клиента
    files_info = []
    for tid in transcription_ids:
        try:
            t = Transcription.objects.get(id=tid)
            # Проверяем, требуется ли подтверждение языка
            requires_language_confirmation = (
                t.detected_language and 
                t.detected_language != 'ru' and 
                not t.language_confirmed and
                t.status == 'pending'
            )
            files_info.append({
                'id': t.id,
                'filename': t.filename,
                'size_mb': round(t.file_size / (1024 * 1024), 2),
                'status': t.status,
                'detected_language': t.detected_language,
                'requires_language_confirmation': requires_language_confirmation
            })
        except:
            pass
    
    return JsonResponse({
        'success': True,
        'transcription_ids': transcription_ids,
        'upload_session': upload_session,
        'count': len(transcription_ids),
        'files': files_info,  # Информация о файлах с размерами
        'message': f'Загружено файлов: {len(transcription_ids)}. Обработка началась.'
    })


def extract_audio(input_path, output_path):
    """Извлечь аудио дорожку из видео/аудио файла используя ffmpeg"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Проверяем наличие ffmpeg
        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path:
            # Пробуем стандартные пути
            possible_paths = ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/bin/ffmpeg']
            for path in possible_paths:
                if os.path.exists(path):
                    ffmpeg_path = path
                    break
        
        if not ffmpeg_path:
            raise Exception("ffmpeg не найден. Убедитесь, что ffmpeg установлен.")
        
        # Используем ffmpeg для извлечения только аудио дорожки
        # -i: входной файл
        # -vn: отключить видео
        # -acodec pcm_s16le: конвертировать в PCM 16-bit (универсальный формат)
        # -ar 16000: частота дискретизации 16kHz (оптимально для Whisper)
        # -ac 1: моно канал
        # -y: перезаписать выходной файл если существует
        # -loglevel error: только ошибки в выводе
        cmd = [
            ffmpeg_path,
            '-i', input_path,
            '-vn',  # Без видео
            '-acodec', 'pcm_s16le',  # PCM 16-bit
            '-ar', '16000',  # 16kHz sample rate
            '-ac', '1',  # Моно
            '-y',  # Перезаписать
            '-loglevel', 'error',  # Только ошибки
            output_path
        ]
        
        logger.info(f"Извлечение аудио: {input_path} -> {output_path}")
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300  # 5 минут максимум
        )
        
        if result.returncode != 0:
            error_msg = result.stderr.decode('utf-8', errors='ignore')
            logger.error(f"Ошибка ffmpeg: {error_msg}")
            raise Exception(f"Ошибка при извлечении аудио: {error_msg[:200]}")
        
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise Exception("Не удалось извлечь аудио дорожку - выходной файл пуст")
        
        logger.info(f"Аудио успешно извлечено: {os.path.getsize(output_path)} байт")
        return True
    except subprocess.TimeoutExpired:
        raise Exception("Превышено время ожидания при извлечении аудио")
    except FileNotFoundError:
        raise Exception("ffmpeg не найден. Убедитесь, что ffmpeg установлен.")


def extract_screenshots_from_video(video_path, transcription_id, output_dir):
    """Извлекает скриншоты из видео используя умную детекцию слайдов через OpenCV"""
    import logging
    import cv2
    import numpy as np
    
    logger = logging.getLogger(__name__)
    
    try:
        # Создаем директорию для скриншотов
        os.makedirs(output_dir, exist_ok=True)
        
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise Exception(f"Не удалось открыть видео файл: {video_path}")
            
        # Получаем параметры видео
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0: fps = 30  # Fallback
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps
        
        logger.info(f"Начало анализа видео: {video_path}, FPS: {fps}, Длительность: {duration:.2f}с")
        
        screenshots = []
        order = 0
        max_screenshots = 1000
        
        # Параметры детекции
        threshold_pixel_diff = 30  # Порог изменения пикселя (0-255)
        threshold_screen_diff = 0.05  # Порог изменения экрана (5%)
        min_time_diff = 2.0  # Минимальное время между слайдами (сек)
        check_interval = 0.5  # Интервал проверки кадров (сек)
        
        last_frame_gray = None
        last_screenshot_time = -min_time_diff  # Чтобы первый кадр тоже мог быть сохранен
        
        frame_step = int(fps * check_interval)
        if frame_step < 1: frame_step = 1
        
        current_frame_idx = 0
        
        while True:
            # Читаем кадр
            ret, frame = cap.read()
            if not ret:
                break
                
            # Пропускаем кадры для ускорения
            if current_frame_idx % frame_step != 0:
                current_frame_idx += 1
                continue
                
            timestamp = current_frame_idx / fps
            
            # Конвертируем в ч/б для сравнения
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Уменьшаем разрешение для ускорения обработки (например до 640px по ширине)
            height, width = gray.shape
            scale = 640 / width if width > 640 else 1
            if scale < 1:
                gray = cv2.resize(gray, (int(width * scale), int(height * scale)))
            
            # Размытие для уменьшения шума
            gray = cv2.GaussianBlur(gray, (21, 21), 0)
            
            is_new_slide = False
            
            if last_frame_gray is None:
                # Всегда сохраняем первый кадр
                is_new_slide = True
            else:
                # Вычисляем разницу
                frame_diff = cv2.absdiff(last_frame_gray, gray)
                
                # Бинаризация разницы
                _, thresh = cv2.threshold(frame_diff, threshold_pixel_diff, 255, cv2.THRESH_BINARY)
                
                # Считаем количество изменившихся пикселей
                changed_pixels = np.count_nonzero(thresh)
                total_pixels = thresh.size
                diff_ratio = changed_pixels / total_pixels
                
                # Если изменение значительно и прошло достаточно времени
                if diff_ratio > threshold_screen_diff and (timestamp - last_screenshot_time) >= min_time_diff:
                    is_new_slide = True
                    logger.info(f"Обнаружен новый слайд на {timestamp:.2f}с (изменение: {diff_ratio:.2%})")
            
            if is_new_slide:
                screenshot_path = os.path.join(output_dir, f"screenshot_{order:04d}.jpg")
                
                # Сохраняем оригинальный кадр (не уменьшенный)
                cv2.imwrite(screenshot_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                
                # Проверяем размер файла
                if os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 0:
                    # Формируем относительный путь
                    if screenshot_path.startswith(str(settings.MEDIA_ROOT)):
                        relative_path = os.path.relpath(screenshot_path, settings.MEDIA_ROOT)
                    else:
                        relative_path = screenshot_path.replace(str(settings.MEDIA_ROOT) + '/', '').replace('/root/media/', '').replace('/var/www/media/', '')
                    
                    relative_path = relative_path.lstrip('/')
                    
                    # Сохраняем в БД
                    from .models import Screenshot
                    screenshot = Screenshot.objects.create(
                        transcription_id=transcription_id,
                        timestamp=timestamp,
                        image_path=relative_path,
                        order=order
                    )
                    screenshots.append(screenshot)
                    
                    last_frame_gray = gray
                    last_screenshot_time = timestamp
                    order += 1
                    
                    if order >= max_screenshots:
                        logger.warning(f"Достигнут лимит скриншотов ({max_screenshots})")
                        break
            
            current_frame_idx += 1
            
        cap.release()
        logger.info(f"Извлечено {len(screenshots)} слайдов из видео")
        return screenshots
        
    except ImportError:
        logger.error("OpenCV не установлен. Установите opencv-python-headless")
        # Fallback на старый метод (ffmpeg) если OpenCV недоступен
        logger.warning("Используем fallback метод (ffmpeg)")
        return extract_screenshots_from_video_ffmpeg_fallback(video_path, transcription_id, output_dir)
        
    except Exception as e:
        logger.error(f"Ошибка при извлечении скриншотов: {e}", exc_info=True)
        return []

def extract_screenshots_from_video_ffmpeg_fallback(video_path, transcription_id, output_dir):
    """Старый метод извлечения (1 раз в минуту) как fallback"""
    import logging
    logger = logging.getLogger(__name__)
    # ... (код старого метода можно оставить здесь или просто вернуть пустой список если не хотим дублировать)
    # Для простоты пока вернем пустой список, так как мы ожидаем что OpenCV будет работать
    return []


def process_file(transcription_id, temp_file_path):
    """Обработка файла в фоновом режиме"""
    import logging
    logger = logging.getLogger(__name__)
    
    transcription = Transcription.objects.get(id=transcription_id)
    transcription.status = 'processing'
    transcription.save()
    
    # Логируем начало обработки
    log_to_elasticsearch('transcription_start', {
        'transcription_id': transcription_id,
        'filename': transcription.filename,
        'file_size': transcription.file_size,
        'whisper_model': transcription.whisper_model,
        'ip_address': transcription.ip_address,
        'user_uuid': transcription.user_uuid
    })
    
    audio_file_path = None
    screenshots_dir = None
    
    try:
        # Проверяем, что файл существует и не пустой
        if not os.path.exists(temp_file_path):
            raise Exception(f"Временный файл не найден: {temp_file_path}")
        
        file_size = os.path.getsize(temp_file_path)
        if file_size == 0:
            raise Exception("Загруженный файл пустой")
        
        # Если нужно извлечь скриншоты и это видео файл
        if transcription.extract_screenshots:
            file_ext = os.path.splitext(temp_file_path)[1].lower()
            video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv']
            if file_ext in video_extensions:
                try:
                    # Устанавливаем статус "в процессе"
                    transcription.screenshot_status = 'processing'
                    transcription.save(update_fields=['screenshot_status'])
                    
                    screenshots_dir = os.path.join(settings.MEDIA_ROOT, 'screenshots', str(transcription_id))
                    extract_screenshots_from_video(temp_file_path, transcription_id, screenshots_dir)
                    
                    # Проверяем результат
                    screenshot_count = transcription.screenshots.count()
                    if screenshot_count > 0:
                        transcription.screenshot_status = 'completed'
                        logger.info(f"Screenshot extraction completed: {screenshot_count} slides extracted")
                    else:
                        transcription.screenshot_status = 'completed'  # Completed but no slides found
                        logger.warning("Screenshot extraction completed but no slides were detected")
                    transcription.save(update_fields=['screenshot_status'])
                except Exception as e:
                    logger.error(f"Error extracting screenshots: {e}", exc_info=True)
                    transcription.screenshot_status = 'error'
                    transcription.save(update_fields=['screenshot_status'])
            else:
                # Not a video file
                transcription.screenshot_status = 'skipped'
                transcription.save(update_fields=['screenshot_status'])
        else:
            # Screenshot extraction not requested
            transcription.screenshot_status = 'skipped'
            transcription.save(update_fields=['screenshot_status'])
        
        # Собираем логи транскрибации
        transcription_logs = []
        from datetime import datetime
        
        def add_log(message, level="INFO"):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] [{level}] {message}"
            transcription_logs.append(log_entry)
            if level == "ERROR":
                logger.error(message)
            elif level == "WARNING":
                logger.warning(message)
            else:
                logger.info(message)
        
        # Извлекаем аудио дорожку в отдельный файл
        # Это гарантирует, что мы транскрибируем именно аудио, а не субтитры
        audio_file_path = temp_file_path + "_audio.wav"
        add_log(f"Начало обработки файла: {transcription.filename}")
        add_log(f"Размер исходного файла: {transcription.file_size} байт ({transcription.file_size / 1024 / 1024:.2f} МБ)")
        add_log(f"Извлечение аудио из файла: {temp_file_path}")
        extract_audio(temp_file_path, audio_file_path)
        
        if not os.path.exists(audio_file_path) or os.path.getsize(audio_file_path) == 0:
            add_log("ОШИБКА: Не удалось извлечь аудио дорожку из файла", "ERROR")
            raise Exception("Не удалось извлечь аудио дорожку из файла")
        
        audio_size = os.path.getsize(audio_file_path)
        add_log(f"Аудио файл успешно создан: {audio_file_path}")
        add_log(f"Размер аудио файла: {audio_size} байт ({audio_size / 1024 / 1024:.2f} МБ)")
        
        # Транскрибируем файл используя выбранную модель
        model_name = transcription.whisper_model or 'base'
        add_log(f"Загрузка модели Whisper: {model_name}")
        model = get_whisper_model(model_name)
        add_log(f"Модель {model_name} успешно загружена")
        
        # Используем более точные параметры для транскрибации
        add_log(f"Начинаем транскрибацию файла {transcription.filename} моделью {model_name}")
        add_log(f"Параметры транскрибации: beam_size=5, language=auto, task=transcribe")
        
        try:
            # Если язык выбран пользователем, используем его
            # Если выбран мультиязыно (auto), используем None (автоопределение)
            # Иначе используем определенный язык
            target_language = None
            if transcription.selected_language:
                # Пользователь выбрал конкретный язык
                target_language = transcription.selected_language
                add_log(f"Используется выбранный пользователем язык: {target_language}")
            elif transcription.detected_language and transcription.language_confirmed:
                # Язык определен автоматически и подтвержден
                target_language = transcription.detected_language
                add_log(f"Используется автоматически определенный язык: {target_language}")
            else:
                # Мультиязыно - автоопределение (Whisper сам определит язык)
                target_language = None
                add_log(f"Используется мультиязыно (автоопределение)")
            
            # Пробуем сначала БЕЗ VAD фильтра (он может быть слишком агрессивным)
            add_log("Попытка транскрибации БЕЗ VAD фильтра")
            segments, info = model.transcribe(
                audio_file_path,
                beam_size=5,
                language=target_language,  # Используем определенный язык или автоопределение
                task="transcribe",
                vad_filter=False,  # Отключаем VAD для начала
            )
            
            add_log(f"Информация о транскрибации (без VAD):")
            add_log(f"  - Определенный язык: {info.language}")
            add_log(f"  - Вероятность языка: {info.language_probability:.4f}")
            add_log(f"  - Длительность аудио: {info.duration:.2f} секунд")
            
            # Сохраняем определенный язык (если еще не сохранен)
            if not transcription.detected_language:
                transcription.detected_language = info.language
                transcription.save(update_fields=['detected_language'])
            
            # Если язык не русский и не подтвержден, останавливаем транскрибацию
            if info.language and info.language != 'ru' and not transcription.language_confirmed:
                add_log(f"Обнаружен не-русский язык ({info.language}). Требуется подтверждение пользователя.", "WARNING")
                # Сохраняем определенный язык, если еще не сохранен
                if not transcription.detected_language:
                    transcription.detected_language = info.language
                transcription.status = 'pending'
                transcription.save(update_fields=['detected_language', 'status'])
                logger.info(f"Транскрибация приостановлена для подтверждения языка: {info.language}")
                return  # Прерываем транскрибацию до подтверждения
            
            # Проверяем, есть ли сегменты
            # ВАЖНО: segments - это итератор, его можно использовать только один раз!
            # Поэтому сразу конвертируем в список
            segment_list = list(segments)
            add_log(f"Найдено сегментов (без VAD): {len(segment_list)}")
            
            if not segment_list or len(segment_list) == 0:
                # Если без VAD нет результатов, пробуем с VAD но с более мягкими параметрами
                add_log("Не найдено сегментов без VAD, пробуем с VAD с мягкими параметрами", "WARNING")
                segments_vad, info = model.transcribe(
                    audio_file_path,
                    beam_size=5,
                    language=None,
                    task="transcribe",
                    vad_filter=True,
                    vad_parameters=dict(
                        min_silence_duration_ms=100,  # Более мягкий порог тишины
                        threshold=0.3  # Более низкий порог для обнаружения речи
                    ),
                )
                add_log(f"Информация о транскрибации (с VAD):")
                add_log(f"  - Определенный язык: {info.language}")
                add_log(f"  - Вероятность языка: {info.language_probability:.4f}")
                add_log(f"  - Длительность аудио: {info.duration:.2f} секунд")
                
                # Сохраняем определенный язык (если еще не сохранен)
                if not transcription.detected_language:
                    transcription.detected_language = info.language
                    transcription.save(update_fields=['detected_language'])
                
                # Если язык не русский и не подтвержден, останавливаем транскрибацию
                if info.language and info.language != 'ru' and not transcription.language_confirmed:
                    add_log(f"Обнаружен не-русский язык ({info.language}). Требуется подтверждение пользователя.", "WARNING")
                    # Сохраняем определенный язык, если еще не сохранен
                    if not transcription.detected_language:
                        transcription.detected_language = info.language
                    transcription.status = 'pending'
                    transcription.save(update_fields=['detected_language', 'status'])
                    logger.info(f"Транскрибация приостановлена для подтверждения языка: {info.language}")
                    return  # Прерываем транскрибацию до подтверждения
                
                segment_list = list(segments_vad)  # Конвертируем в список
                add_log(f"Найдено сегментов (с VAD): {len(segment_list)}")
            else:
                add_log("Транскрибация без VAD успешна, используем эти результаты")
                
        except Exception as e:
            add_log(f"ОШИБКА при вызове model.transcribe: {str(e)}", "ERROR")
            logger.error(f"Ошибка при вызове model.transcribe: {e}", exc_info=True)
            raise Exception(f"Ошибка при транскрибации: {str(e)}")
        
        # Собираем текст из сегментов
        # ВАЖНО: используем segment_list, а не segments (который уже исчерпан)
        text_parts = []
        segment_count = 0
        add_log("Обработка сегментов...")
        for idx, segment in enumerate(segment_list, 1):
            text = segment.text.strip()
            if text:  # Пропускаем пустые сегменты
                text_parts.append(text)
                segment_count += 1
                add_log(f"Сегмент {idx}: время {segment.start:.2f}-{segment.end:.2f}с, текст: {text[:100]}{'...' if len(text) > 100 else ''}")
        
        transcribed_text = " ".join(text_parts).strip()
        
        add_log(f"Транскрибация завершена успешно")
        add_log(f"Всего обработано сегментов: {segment_count}")
        add_log(f"Длина итогового текста: {len(transcribed_text)} символов")
        
        # Сохраняем логи в модель
        transcription.transcription_logs = "\n".join(transcription_logs)
        
        # Если текст пустой или слишком короткий, это может быть ошибка
        # Но не всегда - возможно файл действительно без речи (музыка, шум и т.д.)
        if not transcribed_text:
            # Проверяем, может быть файл слишком короткий или без речи
            add_log(f"ПРЕДУПРЕЖДЕНИЕ: Транскрибация вернула пустой текст. Сегментов: {segment_count}, Язык: {info.language if 'info' in locals() else 'неизвестен'}", "WARNING")
            logger.warning(f"Транскрибация вернула пустой текст. Сегментов: {segment_count}, Язык: {info.language if 'info' in locals() else 'неизвестен'}")
            # НЕ сохраняем предупреждение в текст - сохраняем пустой текст, чтобы пользователь видел проблему
            # transcribed_text = "[Не удалось распознать речь. Возможно, файл не содержит речи или слишком тихий.]"
        elif len(transcribed_text) < 10:
            add_log(f"ПРЕДУПРЕЖДЕНИЕ: Транскрибация вернула очень короткий текст ({len(transcribed_text)} символов). Сегментов: {segment_count}", "WARNING")
            logger.warning(f"Транскрибация вернула очень короткий текст ({len(transcribed_text)} символов). Сегментов: {segment_count}")
            # Сохраняем короткий текст как есть, без предупреждения в тексте
        
        # Обновляем запись
        transcription.transcribed_text = transcribed_text
        transcription.status = 'completed'
        transcription.save()
        
        logger.info(f"Транскрибация завершена для файла {transcription.filename}. Сегментов: {segment_count}, Длина текста: {len(transcribed_text)}")
        
        # Уменьшаем баланс на 1 при успешном завершении транскрибации
        ip_counter = None
        uuid_counter = None
        try:
            ip_counter = IPUploadCount.get_or_create_for_ip(transcription.ip_address)
            if ip_counter.balance > 0:
                ip_counter.balance -= 1
                ip_counter.save()
                logger.info(f"Баланс IP {transcription.ip_address} уменьшен на 1. Остаток: {ip_counter.balance}")
            
            if transcription.user_uuid:
                uuid_counter = UUIDUploadCount.get_or_create_for_uuid(transcription.user_uuid)
                if uuid_counter.balance > 0:
                    uuid_counter.balance -= 1
                    uuid_counter.save()
                    logger.info(f"Баланс UUID {transcription.user_uuid} уменьшен на 1. Остаток: {uuid_counter.balance}")
        except Exception as e:
            logger.error(f"Ошибка при уменьшении баланса: {e}", exc_info=True)
        
        # Логируем завершение транскрибации
        duration = None
        if 'info' in locals():
            duration = info.duration
        log_to_elasticsearch('transcription_complete', {
            'transcription_id': transcription_id,
            'filename': transcription.filename,
            'segment_count': segment_count,
            'text_length': len(transcribed_text),
            'detected_language': transcription.detected_language,
            'language_confirmed': transcription.language_confirmed,
            'duration_seconds': duration,
            'ip_balance_after': ip_counter.balance if ip_counter else None,
            'uuid_balance_after': uuid_counter.balance if uuid_counter else None
        })
        
        # Удаляем старые файлы, сохраняя последние 2
        #         cleanup_old_files(transcription)
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        transcription.status = 'error'
        transcription.error_message = error_msg
        transcription.save()
        logger.error(f"Ошибка при обработке файла {transcription.filename}: {error_msg}", exc_info=True)
        
        # Логируем ошибку в Elasticsearch
        log_to_elasticsearch('transcription_error', {
            'transcription_id': transcription_id,
            'filename': transcription.filename if transcription else 'unknown',
            'error_type': type(e).__name__,
            'error_message': str(e)
        }, level='error')
    finally:
        # Удаляем временные файлы (но не скриншоты)
        # Удаляем только audio_file_path (temp_file_path - это original_file_path, он должен сохраняться)
        for file_path in [audio_file_path]:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Ошибка при удалении временного файла {file_path}: {e}")


def cleanup_old_files(current_transcription):
    """Удаляет старые файлы, сохраняя последние 2 загруженных файла (всегда, независимо от статуса)"""
    try:
        # Получаем все транскрипции с файлами, отсортированные по дате загрузки (новые первые)
        # Включаем все статусы - сохраняем последние 2 файла всегда
        all_transcriptions = Transcription.objects.filter(
            original_file_path__isnull=False
        ).exclude(
            id=current_transcription.id
        ).order_by('-uploaded_at')
        
        # Оставляем последние 2 файла, остальные удаляем
        transcriptions_to_delete = all_transcriptions[2:]
        
        deleted_count = 0
        for transcription in transcriptions_to_delete:
            if transcription.original_file_path and os.path.exists(transcription.original_file_path):
                try:
                    # Удаляем файл и его директорию
                    file_dir = os.path.dirname(transcription.original_file_path)
                    if os.path.exists(file_dir):
                        shutil.rmtree(file_dir)
                        deleted_count += 1
                        logger.info(f"Удален старый файл: {transcription.original_file_path}")
                        log_to_elasticsearch('file_cleanup', {
                            'transcription_id': transcription.id,
                            'filename': transcription.filename,
                            'file_path': transcription.original_file_path,
                            'action': 'deleted'
                        })
                except Exception as e:
                    logger.error(f"Ошибка при удалении файла {transcription.original_file_path}: {e}")
                    log_to_elasticsearch('file_cleanup_error', {
                        'transcription_id': transcription.id,
                        'filename': transcription.filename,
                        'file_path': transcription.original_file_path,
                        'error': str(e)
                    })
        
        if deleted_count > 0:
            logger.info(f"Очистка файлов: удалено {deleted_count} старых файлов, сохранено последних 2")
            log_to_elasticsearch('file_cleanup_summary', {
                'deleted_count': deleted_count,
                'kept_count': 2
            })
    except Exception as e:
        logger.error(f"Ошибка при очистке старых файлов: {e}", exc_info=True)
        log_to_elasticsearch('file_cleanup_error', {
            'error': str(e),
            'type': 'cleanup_exception'
        })


# get_client_ip перенесена в utils.py


def transcription_detail(request, transcription_id):
    """Детальная информация о транскрипции"""
    try:
        transcription = Transcription.objects.get(id=transcription_id)
        return render(request, 'transcribe/detail.html', {
            'transcription': transcription
        })
    except Transcription.DoesNotExist:
        return HttpResponse("Транскрипция не найдена", status=404)


def transcription_status(request, transcription_id):
    """Получить статус транскрипции (для AJAX запросов)"""
    try:
        transcription = Transcription.objects.get(id=transcription_id)
        
        # Проверяем доступ
        active_password_phrase = request.session.get('password_phrase', None)
        if transcription.password_phrase_hash:
            if not active_password_phrase or not transcription.check_password_phrase(active_password_phrase):
                return JsonResponse({'error': 'Доступ запрещен'}, status=403)
        
        # Проверяем, требуется ли подтверждение языка
        requires_language_confirmation = (
            transcription.detected_language and 
            transcription.detected_language != 'ru' and 
            not transcription.language_confirmed and
            transcription.status == 'pending'
        )
        
        return JsonResponse({
            'status': transcription.status,
            'text': transcription.transcribed_text if transcription.status == 'completed' else None,
            'error': transcription.error_message if transcription.status == 'error' else None,
            'detected_language': transcription.detected_language,
            'language_confirmed': transcription.language_confirmed,
            'requires_language_confirmation': requires_language_confirmation,
            'screenshot_status': transcription.screenshot_status if transcription.extract_screenshots else 'skipped',
            'screenshot_count': transcription.screenshots.count() if transcription.extract_screenshots else 0,
        })
    except Transcription.DoesNotExist:
        return JsonResponse({'error': 'Транскрипция не найдена'}, status=404)


@require_http_methods(["POST"])
def login_with_phrase(request):
    """Вход по фразе-паролю"""
    password_phrase = request.POST.get('password_phrase', '').strip()
    
    if not password_phrase:
        return JsonResponse({'error': 'Введите фразу-пароль'}, status=400)
    
    # Сохраняем фразу-пароль в сессии
    request.session['password_phrase'] = password_phrase
    request.session.save()
    
    return JsonResponse({
        'success': True,
        'message': 'Вход выполнен успешно'
    })


@require_http_methods(["POST"])
def confirm_language(request, transcription_id):
    """Подтверждение языка для продолжения транскрибации"""
    import json
    try:
        transcription = Transcription.objects.get(id=transcription_id)
        
        # Проверяем доступ
        active_password_phrase = request.session.get('password_phrase', None)
        if transcription.password_phrase_hash:
            if not active_password_phrase or not transcription.check_password_phrase(active_password_phrase):
                return JsonResponse({'error': 'Доступ запрещен'}, status=403)
        
        # Получаем выбранный язык из запроса
        try:
            body = json.loads(request.body)
            language_mode = body.get('language_mode', 'auto')  # 'auto' или 'specific'
            selected_language = body.get('selected_language', None)  # код языка, если выбран конкретный
        except:
            language_mode = request.POST.get('language_mode', 'auto')
            selected_language = request.POST.get('selected_language', None)
        
        # Сохраняем выбранный язык
        if language_mode == 'specific' and selected_language:
            transcription.selected_language = selected_language
        else:
            transcription.selected_language = None  # Мультиязыно (auto)
        
        # Подтверждаем язык и продолжаем транскрибацию
        transcription.language_confirmed = True
        transcription.status = 'pending'  # Возвращаем в pending для продолжения обработки
        transcription.save(update_fields=['language_confirmed', 'status', 'selected_language'])
        
        # Запускаем обработку заново
        if transcription.original_file_path and os.path.exists(transcription.original_file_path):
            thread = threading.Thread(target=process_file, args=(transcription.id, transcription.original_file_path))
            thread.daemon = True
            thread.start()
            return JsonResponse({
                'success': True,
                'message': 'Язык подтвержден. Транскрибация продолжается.'
            })
        else:
            return JsonResponse({
                'error': 'Оригинальный файл не найден. Невозможно продолжить транскрибацию.'
            }, status=404)
        
    except Transcription.DoesNotExist:
        return JsonResponse({'error': 'Транскрипция не найдена'}, status=404)


@require_http_methods(["GET", "POST"])
def check_balance(request):
    """Проверка баланса пользователя"""
    try:
        if request.method == 'GET':
            user_uuid = request.GET.get('user_uuid', '').strip()
        else:
            import json
            try:
                body = json.loads(request.body)
                user_uuid = body.get('user_uuid', '').strip()
            except:
                user_uuid = request.POST.get('user_uuid', '').strip()
        
        if not user_uuid:
            return JsonResponse({'error': 'UUID не передан'}, status=400)
        
        ip_address = get_client_ip(request)
        
        # Получаем балансы
        try:
            ip_counter = IPUploadCount.get_or_create_for_ip(ip_address)
        except Exception as e:
            logger.error(f"Error creating IP counter for {ip_address}: {e}")
            # Fallback for invalid IP
            ip_counter = None
            
        uuid_counter = UUIDUploadCount.get_or_create_for_uuid(user_uuid)
        
        ip_balance = ip_counter.balance if ip_counter else 0
        uuid_balance = uuid_counter.balance if uuid_counter else 0
        total_balance = max(ip_balance, uuid_balance)  # Используем максимальный баланс
        
        return JsonResponse({
            'success': True,
            'ip_balance': ip_balance,
            'uuid_balance': uuid_balance,
            'balance': total_balance,
            'has_balance': total_balance > 0
        })
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Critical error in check_balance: {e}\n{error_details}")
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["POST"])
def logout_phrase(request):
    """Выход из режима фразы-пароля или смена фразы-пароля"""
    import json
    try:
        body = json.loads(request.body)
        action = body.get('action', 'logout')
    except:
        action = 'logout'
    
    if 'password_phrase' in request.session:
        del request.session['password_phrase']
        request.session.save()
    
    message = 'Выход выполнен' if action == 'logout' else 'Фраза-пароль сброшена. Войдите с новой фразой.'
    
    return JsonResponse({
        'success': True,
        'message': message
    })


def transcription_detail(request, transcription_id=None, public_token=None):
    """Детальная информация о транскрипции"""
    try:
        # Если передан публичный токен в URL, используем его
        # public_token может быть строкой (токен) или True (из urls.py)
        if public_token and public_token is not True:
            # Получаем токен из URL параметра
            transcription = Transcription.objects.get(public_token=public_token)
            
            # Проверяем, есть ли параметр пароля в URL
            password_token = request.GET.get('p', None)
            if password_token and transcription.password_phrase_hash:
                # Проверяем токен пароля
                import hashlib
                expected_token = hashlib.sha256(f"{transcription.public_token}_{transcription.password_phrase_hash}".encode()).hexdigest()[:16]
                if password_token == expected_token:
                    # Автоматически входим по паролю для этой сессии
                    request.session['password_phrase'] = 'public_access'
                    is_public_access = True
                else:
                    return HttpResponse("Неверная ссылка доступа", status=403)
            elif transcription.password_phrase_hash:
                # Если есть пароль, но токен не передан - доступ запрещен
                return HttpResponse("Для доступа к этой транскрипции нужна специальная ссылка с паролем", status=403)
            else:
                # Публичные ссылки без пароля отключены
                return HttpResponse("Публичный доступ к этой транскрипции недоступен", status=403)
        elif transcription_id:
            transcription = Transcription.objects.get(id=transcription_id)
            is_public_access = False
            
            # Проверяем доступ
            active_password_phrase = request.session.get('password_phrase', None)
            if transcription.password_phrase_hash:
                if not active_password_phrase or not transcription.check_password_phrase(active_password_phrase):
                    return HttpResponse("Доступ запрещен. Необходимо войти по фразе-паролю.", status=403)
        else:
            return HttpResponse("Транскрипция не найдена", status=404)
        
        # Получаем скриншоты если есть
        screenshots = transcription.screenshots.all().order_by('order', 'timestamp')
        
        # Получаем файлы из той же сессии загрузки (если есть)
        related_transcriptions = []
        if transcription.upload_session:
            related_transcriptions = Transcription.objects.filter(
                upload_session=transcription.upload_session
            ).exclude(id=transcription_id).order_by('uploaded_at')
        
        # Разбиваем текст на части для комикса (по предложениям)
        text_parts = []
        if transcription.transcribed_text:
            import re
            # Разбиваем по предложениям
            sentences = re.split(r'([.!?]+)', transcription.transcribed_text)
            current_text = ""
            for i in range(0, len(sentences), 2):
                if i < len(sentences):
                    sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else "")
                    current_text += sentence.strip() + " "
                    # Создаем слайд каждые ~100 символов или по предложениям
                    if len(current_text) > 100 or i+2 >= len(sentences):
                        if current_text.strip():
                            text_parts.append(current_text.strip())
                        current_text = ""
        
        # Если скриншотов больше, чем текстовых частей, создаем слайды для каждого скриншота
        slides = []
        max_items = max(len(screenshots), len(text_parts), 1)
        
        for i in range(max_items):
            slide = {
                'number': i + 1,
                'text': text_parts[i] if i < len(text_parts) else "",
                'screenshot': screenshots[i] if i < len(screenshots) else None,
                'timestamp': screenshots[i].timestamp if i < len(screenshots) and screenshots[i] else None
            }
            slides.append(slide)
        
        # Если нет слайдов, создаем один с текстом
        if not slides and transcription.transcribed_text:
            slides.append({
                'number': 1,
                'text': transcription.transcribed_text,
                'screenshot': None,
                'timestamp': None
            })
        
        # Генерируем публичный токен если его нет
        if not transcription.public_token:
            transcription.generate_public_token()
        
        active_password_phrase = request.session.get('password_phrase', None) if not is_public_access else None
        
        # Формируем публичную ссылку только если есть пароль
        # Публичные ссылки без пароля убраны - показываем только ссылку с паролем
        public_url_with_password = None
        if transcription.password_phrase_hash and transcription.public_token:
            # Создаем специальный токен для доступа с паролем
            # Используем хеш пароля как часть токена для безопасности
            import hashlib
            password_token = hashlib.sha256(f"{transcription.public_token}_{transcription.password_phrase_hash}".encode()).hexdigest()[:16]
            public_url_with_password = request.build_absolute_uri(f'/public/{transcription.public_token}/?p={password_token}')
        
        from django.conf import settings
        
        return render(request, 'transcribe/detail.html', {
            'transcription': transcription,
            'is_logged_in': active_password_phrase is not None if not is_public_access else False,
            'is_public_access': is_public_access,
            'slides': slides,
            'total_slides': len(slides),
            'related_transcriptions': related_transcriptions,
            'upload_session': transcription.upload_session,
            'public_url': None,  # Убрана публичная ссылка без пароля
            'public_url_with_password': public_url_with_password,
            'has_password': bool(transcription.password_phrase_hash),
            'MEDIA_URL': settings.MEDIA_URL,
            'transcription_logs': transcription.transcription_logs  # Передаем логи в шаблон
        })
    except Transcription.DoesNotExist:
        return HttpResponse("Транскрипция не найдена", status=404)


def download_text(request, transcription_id=None, public_token=None):
    """Скачать только текст транскрипции"""
    try:
        if public_token and public_token is not True:
            transcription = Transcription.objects.get(public_token=public_token)
            # Проверяем параметр пароля если есть
            password_token = request.GET.get('p', None)
            if password_token and transcription.password_phrase_hash:
                import hashlib
                expected_token = hashlib.sha256(f"{transcription.public_token}_{transcription.password_phrase_hash}".encode()).hexdigest()[:16]
                if password_token != expected_token:
                    return HttpResponse("Доступ запрещен", status=403)
            elif transcription.password_phrase_hash:
                return HttpResponse("Доступ запрещен", status=403)
        else:
            transcription = Transcription.objects.get(id=transcription_id)
            # Проверяем доступ
            active_password_phrase = request.session.get('password_phrase', None)
            if transcription.password_phrase_hash:
                if not active_password_phrase or not transcription.check_password_phrase(active_password_phrase):
                    return HttpResponse("Доступ запрещен", status=403)
        
        response = HttpResponse(transcription.transcribed_text or '', content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{transcription.filename}_transcription.txt"'
        return response
    except Transcription.DoesNotExist:
        return HttpResponse("Транскрипция не найдена", status=404)


def download_screenshots(request, transcription_id=None, public_token=None):
    """Скачать все скриншоты как архив"""
    import zipfile
    import io
    
    try:
        if public_token and public_token is not True:
            transcription = Transcription.objects.get(public_token=public_token)
            # Проверяем параметр пароля если есть
            password_token = request.GET.get('p', None)
            if password_token and transcription.password_phrase_hash:
                import hashlib
                expected_token = hashlib.sha256(f"{transcription.public_token}_{transcription.password_phrase_hash}".encode()).hexdigest()[:16]
                if password_token != expected_token:
                    return HttpResponse("Доступ запрещен", status=403)
            elif transcription.password_phrase_hash:
                return HttpResponse("Доступ запрещен", status=403)
        else:
            transcription = Transcription.objects.get(id=transcription_id)
            # Проверяем доступ
            active_password_phrase = request.session.get('password_phrase', None)
            if transcription.password_phrase_hash:
                if not active_password_phrase or not transcription.check_password_phrase(active_password_phrase):
                    return HttpResponse("Доступ запрещен", status=403)
        
        screenshots = transcription.screenshots.all().order_by('order', 'timestamp')
        
        if not screenshots:
            return HttpResponse("Скриншоты не найдены", status=404)
        
        # Создаем ZIP архив в памяти
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for screenshot in screenshots:
                # Пробуем разные варианты путей
                image_path = None
                possible_paths = [
                    os.path.join(settings.MEDIA_ROOT, screenshot.image_path),
                    screenshot.image_path,  # Если путь уже абсолютный
                    os.path.join(settings.MEDIA_ROOT, screenshot.image_path.lstrip('/')),
                ]
                
                for path in possible_paths:
                    if os.path.exists(path) and os.path.isfile(path):
                        image_path = path
                        break
                
                if image_path and os.path.exists(image_path):
                    try:
                        zip_file.write(image_path, f"screenshot_{screenshot.order:04d}_{screenshot.timestamp:.0f}s.jpg")
                    except Exception as e:
                        logger.warning(f"Не удалось добавить скриншот {image_path} в архив: {e}")
                        continue
        
        zip_buffer.seek(0)
        response = HttpResponse(zip_buffer.read(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{transcription.filename}_screenshots.zip"'
        return response
    except Transcription.DoesNotExist:
        return HttpResponse("Транскрипция не найдена", status=404)


@require_http_methods(["POST"])
def retranscribe(request, transcription_id):
    """Перетранскрибировать файл с другой моделью"""
    import json
    import os
    import tempfile
    from django.conf import settings
    
    try:
        transcription = Transcription.objects.get(id=transcription_id)
        
        # Проверяем доступ
        from .utils import check_transcription_access
        has_access, _, error_msg = check_transcription_access(transcription, request, require_password=False)
        if not has_access:
            return JsonResponse({'error': error_msg or 'Доступ запрещен'}, status=403)
        
        # Получаем новую модель из запроса
        body = json.loads(request.body)
        new_model = body.get('model', 'base')
        
        # Валидируем модель
        valid_models = [choice[0] for choice in Transcription.WHISPER_MODELS]
        if new_model not in valid_models:
            return JsonResponse({'error': f'Неверная модель. Доступные: {", ".join(valid_models)}'}, status=400)
        
        # Проверяем, что файл не обрабатывается
        if transcription.status == 'processing':
            return JsonResponse({'error': 'Файл уже обрабатывается'}, status=400)
        
        # Ищем оригинальный файл
        original_file_path = None
        
        # Сначала проверяем сохраненный путь в БД
        if transcription.original_file_path:
            if os.path.exists(transcription.original_file_path):
                original_file_path = transcription.original_file_path
                logger.info(f"Файл найден по пути из БД: {original_file_path}")
            else:
                logger.warning(f"Файл по пути из БД не существует: {transcription.original_file_path}")
        
        # Если путь в БД не работает, ищем в разных местах
        if not original_file_path:
            temp_dir = tempfile.gettempdir()
            file_ext = os.path.splitext(transcription.filename)[1]
            possible_paths = [
                # Проверяем все возможные пути в uploads
                os.path.join(settings.MEDIA_ROOT, 'uploads', '*', f'original{file_ext}'),
                os.path.join(settings.MEDIA_ROOT, 'uploads', '*', transcription.filename),
                # Временные файлы
                os.path.join(temp_dir, f'transcription_{transcription.id}_{transcription.filename}'),
                os.path.join('/tmp', f'transcription_{transcription.id}_{transcription.filename}'),
            ]
            
            import glob
            for pattern in possible_paths:
                matches = glob.glob(pattern)
                for match in matches:
                    if os.path.isfile(match):
                        # Проверяем размер файла
                        try:
                            match_size = os.path.getsize(match)
                            if match_size == transcription.file_size or match_size > 0:
                                original_file_path = match
                                # Обновляем путь в БД для будущих использований
                                transcription.original_file_path = match
                                transcription.save(update_fields=['original_file_path'])
                                logger.info(f"Файл найден по паттерну: {match}, размер: {match_size}")
                                break
                        except:
                            continue
                if original_file_path:
                    break
        
        if not original_file_path:
            # Если файл не найден, возвращаем ошибку
            return JsonResponse({
                'error': 'Оригинальный файл не найден на сервере. Файл мог быть удален или перемещен.'
            }, status=404)
        
        # Обновляем модель и статус
        transcription.whisper_model = new_model
        transcription.status = 'pending'
        transcription.transcribed_text = ''
        transcription.error_message = None
        transcription.transcription_logs = None  # Очищаем старые логи
        transcription.save()
        
        # Запускаем обработку в отдельном потоке
        thread = threading.Thread(target=process_file, args=(transcription.id, original_file_path))
        thread.daemon = True
        thread.start()
        
        logger.info(f"Перетранскрибация запущена: ID={transcription_id}, модель={new_model}, файл={original_file_path}")
        
        return JsonResponse({
            'success': True,
            'message': f'Перетранскрибация запущена с моделью {new_model}',
            'transcription_id': transcription.id
        })
        
    except Transcription.DoesNotExist:
        return JsonResponse({'error': 'Транскрипция не найдена'}, status=404)
    except Exception as e:
        logger.error(f"Ошибка при перетранскрибации: {e}", exc_info=True)
        return JsonResponse({'error': f'Ошибка при перетранскрибации: {str(e)}'}, status=500)


@require_http_methods(["POST"])
def process_payment(request):
    """Обработка оплаты (пока заглушка, готово для интеграции с API)"""
    import json
    
    try:
        body = json.loads(request.body)
        user_uuid = body.get('user_uuid', '').strip()
        ip_address = get_client_ip(request)
        payment_data = body.get('payment_data', {})
        
        if not user_uuid:
            return JsonResponse({'error': 'UUID не передан'}, status=400)
        
        # Получаем данные карты
        card_number = payment_data.get('card_number', '').replace(' ', '').replace('-', '')
        card_expiry = payment_data.get('card_expiry', '').replace('/', '')
        card_cvc = payment_data.get('card_cvc', '')
        card_holder = payment_data.get('card_holder', '').strip()
        
        # Проверяем, являются ли все поля нулями (тестовый режим)
        # Нормализуем данные для проверки
        card_number_clean = card_number.replace(' ', '').replace('-', '').strip()
        card_expiry_clean = card_expiry.replace('/', '').replace(' ', '').strip()
        card_cvc_clean = card_cvc.strip()
        card_holder_clean = card_holder.strip().upper()
        
        is_test_payment = (
            card_number_clean == '0000000000000000' and
            card_expiry_clean == '0000' and
            card_cvc_clean == '000' and
            (card_holder_clean == '0' or card_holder_clean.replace('0', '').replace(' ', '').strip() == '' or not card_holder_clean)
        )
        
        if is_test_payment:
            # Тестовая оплата - помечаем как оплачено и добавляем +3 к балансу
            try:
                ip_counter = IPUploadCount.get_or_create_for_ip(ip_address)
                uuid_counter = UUIDUploadCount.get_or_create_for_uuid(user_uuid)
                
                ip_counter.is_paid = True
                ip_counter.balance += 3  # Добавляем 3 транскрибации к балансу
                ip_counter.save()
                
                uuid_counter.is_paid = True
                uuid_counter.balance += 3  # Добавляем 3 транскрибации к балансу
                uuid_counter.save()
                
                # Логируем тестовую оплату
                logger.info(f"Тестовая оплата обработана: IP={ip_address}, UUID={user_uuid}, баланс IP={ip_counter.balance}, баланс UUID={uuid_counter.balance}")
                log_to_elasticsearch('payment_success', {
                    'payment_type': 'test',
                    'ip_address': ip_address,
                    'user_uuid': user_uuid,
                    'balance_added': 3,
                    'ip_balance': ip_counter.balance,
                    'uuid_balance': uuid_counter.balance,
                    'payment_id': 'test_payment_' + str(uuid.uuid4())[:8]
                })
                
                return JsonResponse({
                    'success': True,
                    'message': 'Оплата успешно обработана. Вам добавлено 3 транскрибации. Теперь вы можете загружать файлы.',
                    'payment_id': 'test_payment_' + str(uuid.uuid4())[:8],
                    'balance': uuid_counter.balance
                })
            except Exception as e:
                logger.error(f"Ошибка при обработке тестовой оплаты: {e}", exc_info=True)
                log_to_elasticsearch('payment_error', {
                    'payment_type': 'test',
                    'ip_address': ip_address,
                    'user_uuid': user_uuid,
                    'error': str(e)
                }, level='error')
                return JsonResponse({
                    'error': f'Ошибка при обработке оплаты: {str(e)}'
                }, status=500)
        else:
            # Реальная оплата - пока возвращаем ошибку, т.к. API не интегрирован
            # TODO: Интеграция с платежным API согласно api_integration.pdf
            logger.warning(f"Попытка реальной оплаты без интеграции API: IP={ip_address}, UUID={user_uuid}")
            log_to_elasticsearch('payment_rejected', {
                'payment_type': 'real',
                'ip_address': ip_address,
                'user_uuid': user_uuid,
                'reason': 'API not integrated'
            })
            return JsonResponse({
                'error': 'Платежная система временно недоступна. Используйте тестовые данные для проверки (все нули).'
            }, status=400)
        
    except Exception as e:
        logger.error(f"Ошибка при обработке оплаты: {e}", exc_info=True)
        return JsonResponse({'error': f'Ошибка при обработке оплаты: {str(e)}'}, status=500)


def download_session_text(request, upload_session):
    """Скачать общий текст всех файлов из одной сессии загрузки"""
    try:
        transcriptions = Transcription.objects.filter(upload_session=upload_session).order_by('uploaded_at')
        
        if not transcriptions:
            return HttpResponse("Транскрипции не найдены", status=404)
        
        # Проверяем доступ
        active_password_phrase = request.session.get('password_phrase', None)
        for transcription in transcriptions:
            if transcription.password_phrase_hash:
                if not active_password_phrase or not transcription.check_password_phrase(active_password_phrase):
                    return HttpResponse("Доступ запрещен", status=403)
        
        # Объединяем все тексты
        all_texts = []
        for transcription in transcriptions:
            if transcription.transcribed_text:
                all_texts.append(f"=== {transcription.filename} ===\n{transcription.transcribed_text}\n\n")
        
        combined_text = "\n".join(all_texts)
        
        response = HttpResponse(combined_text, content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="session_{upload_session[:8]}_all_transcriptions.txt"'
        return response
    except Exception as e:
        return HttpResponse(f"Ошибка: {str(e)}", status=500)


@require_http_methods(["POST"])
def clear_disk(request):
    """Очистка диска - удаление всех транскрипций и медиа-файлов"""
    import json
    from django.contrib.auth import get_user_model
    
    try:
        body = json.loads(request.body)
        password = body.get('password', '')
        
        if not password:
            return JsonResponse({'success': False, 'error': 'Пароль не указан'}, status=400)
        
        # Проверяем пароль суперадмина
        User = get_user_model()
        try:
            admin_user = User.objects.filter(is_superuser=True).first()
            if not admin_user or not admin_user.check_password(password):
                return JsonResponse({'success': False, 'error': 'Неверный пароль'}, status=403)
        except Exception as e:
            logger.error(f"Ошибка при проверке пароля: {e}")
            return JsonResponse({'success': False, 'error': 'Ошибка проверки пароля'}, status=500)
        
        # Удаляем все транскрипции и связанные скриншоты
        deleted_count = 0
        screenshots_deleted = 0
        
        transcriptions = Transcription.objects.all()
        for transcription in transcriptions:
            # Удаляем скриншоты
            screenshots = transcription.screenshots.all()
            for screenshot in screenshots:
                screenshot_path = os.path.join(settings.MEDIA_ROOT, screenshot.image_path)
                if os.path.exists(screenshot_path):
                    try:
                        os.remove(screenshot_path)
                        screenshots_deleted += 1
                    except Exception as e:
                        logger.error(f"Ошибка при удалении скриншота {screenshot_path}: {e}")
            deleted_count += 1
        
        # Удаляем все записи из БД
        Transcription.objects.all().delete()
        
        # Очищаем директорию скриншотов
        screenshots_dir = os.path.join(settings.MEDIA_ROOT, 'screenshots')
        if os.path.exists(screenshots_dir):
            try:
                import shutil
                for item in os.listdir(screenshots_dir):
                    item_path = os.path.join(screenshots_dir, item)
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    elif os.path.isfile(item_path):
                        os.remove(item_path)
            except Exception as e:
                logger.error(f"Ошибка при очистке директории скриншотов: {e}")
        
        logger.info(f"Диск очищен: удалено транскрипций: {deleted_count}, скриншотов: {screenshots_deleted}")
        
        return JsonResponse({
            'success': True,
            'message': f'Удалено транскрипций: {deleted_count}, скриншотов: {screenshots_deleted}'
        })
        
    except Exception as e:
        logger.error(f"Ошибка при очистке диска: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def transcription_view(request, transcription_id=None, public_token=None):
    """Адаптивная HTML страница с чередованием слайдов и текста"""
    from django.conf import settings
    from django.http import HttpResponse
    from django.shortcuts import render
    from .models import Transcription
    import re
    
    try:
        if public_token and public_token is not True:
            transcription = Transcription.objects.get(public_token=public_token)
            password_token = request.GET.get('p', None)
            if password_token and transcription.password_phrase_hash:
                import hashlib
                expected_token = hashlib.sha256(f"{transcription.public_token}_{transcription.password_phrase_hash}".encode()).hexdigest()[:16]
                if password_token != expected_token:
                    return HttpResponse("Доступ запрещен", status=403)
            elif transcription.password_phrase_hash:
                return HttpResponse("Доступ запрещен", status=403)
        elif transcription_id:
            transcription = Transcription.objects.get(id=transcription_id)
            active_password_phrase = request.session.get('password_phrase', None)
            if transcription.password_phrase_hash:
                if not active_password_phrase or not transcription.check_password_phrase(active_password_phrase):
                    return HttpResponse("Доступ запрещен", status=403)
        else:
            return HttpResponse("Транскрипция не найдена", status=404)
        
        screenshots = list(transcription.screenshots.all().order_by('order', 'timestamp'))
        screenshot_count = len(screenshots)
        
        text_blocks = []
        if transcription.transcribed_text:
            if screenshot_count > 0:
                text = transcription.transcribed_text.strip()
                sentences = re.split(r'([.!?]+(?:\s|$))', text)
                clean_sentences = []
                for i in range(0, len(sentences), 2):
                    if i < len(sentences):
                        sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else "")
                        if sentence.strip():
                            clean_sentences.append(sentence.strip())
                
                if clean_sentences:
                    sentences_per_block = max(1, len(clean_sentences) // screenshot_count) if screenshot_count > 0 else 1
                    for i in range(screenshot_count):
                        start_idx = i * sentences_per_block
                        end_idx = (i + 1) * sentences_per_block if i < screenshot_count - 1 else len(clean_sentences)
                        block_text = " ".join(clean_sentences[start_idx:end_idx])
                        text_blocks.append(block_text)
                else:
                    chars_per_block = max(1, len(text) // screenshot_count) if screenshot_count > 0 else len(text)
                    for i in range(screenshot_count):
                        start_idx = i * chars_per_block
                        end_idx = (i + 1) * chars_per_block if i < screenshot_count - 1 else len(text)
                        text_blocks.append(text[start_idx:end_idx])
            else:
                text_blocks.append(transcription.transcribed_text)
        
        slides = []
        for i in range(screenshot_count):
            slides.append({'type': 'screenshot', 'screenshot': screenshots[i], 'number': len(slides) + 1})
            text_content = text_blocks[i] if i < len(text_blocks) else ""
            slides.append({'type': 'text', 'text': text_content, 'number': len(slides) + 1})
        
        if screenshot_count == 0 and text_blocks:
            for i, text_block in enumerate(text_blocks):
                slides.append({'type': 'text', 'text': text_block, 'number': i + 1})
        
        return render(request, 'transcribe/view.html', {
            'transcription': transcription,
            'slides': slides,
            'total_slides': len(slides),
            'MEDIA_URL': settings.MEDIA_URL,
        })
    except Transcription.DoesNotExist:
        return HttpResponse("Транскрипция не найдена", status=404)

@require_http_methods(["POST"])
def upload_from_url(request):
    """Обработка загрузки файлов по URL (поддерживает cloud.mail.ru и прямые ссылки)"""
    import json
    import uuid
    import shutil
    from django.http import JsonResponse
    from .models import Transcription, IPUploadCount, UUIDUploadCount
    from .utils import get_client_ip, validate_whisper_model
    from .upload_url import download_from_url
    from django.conf import settings
    import os
    import threading
    from .views import process_file
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        try:
            body = json.loads(request.body)
            urls = body.get('urls', [])
            if isinstance(urls, str):
                urls = [urls]
        except:
            urls = request.POST.getlist('urls')
            if isinstance(urls, str):
                urls = [urls.split(',')] if ',' in urls else [urls]
        
        if not urls or len(urls) == 0:
            return JsonResponse({'error': 'URL не указан'}, status=400)
        
        ip_address = get_client_ip(request)
        
        try:
            user_uuid = body.get('user_uuid', '') if 'body' in locals() else request.POST.get('user_uuid', '')
        except:
            user_uuid = request.POST.get('user_uuid', '')
        
        if not user_uuid:
            return JsonResponse({'error': 'UUID не передан'}, status=400)
        
        ip_counter = IPUploadCount.get_or_create_for_ip(ip_address)
        ip_monthly_count = ip_counter.get_monthly_count()
        uuid_counter = UUIDUploadCount.get_or_create_for_uuid(user_uuid)
        uuid_monthly_count = uuid_counter.get_monthly_count()
        ip_balance = ip_counter.balance if ip_counter else 0
        uuid_balance = uuid_counter.balance if uuid_counter else 0
        has_balance = ip_balance > 0 or uuid_balance > 0
        requires_payment = False
        if ip_monthly_count >= 2 and not ip_counter.is_paid and not has_balance:
            requires_payment = True
        elif uuid_monthly_count >= 2 and not uuid_counter.is_paid and not has_balance:
            requires_payment = True
        
        if requires_payment:
            return JsonResponse({
                'error': 'Для продолжения использования сервиса требуется оплата 12 рублей. Пожалуйста, произведите оплату.',
                'requires_payment': True,
                'ip_count': ip_monthly_count,
                'uuid_count': uuid_monthly_count,
                'ip_balance': ip_balance,
                'uuid_balance': uuid_balance
            }, status=402)
        
        try:
            signature = body.get('signature', '') if 'body' in locals() else request.POST.get('signature', '')
            password_phrase = body.get('password_phrase', '') if 'body' in locals() else request.POST.get('password_phrase', '')
            extract_screenshots = body.get('extract_screenshots', False) if 'body' in locals() else request.POST.get('extract_screenshots') == 'on'
            whisper_model, _ = validate_whisper_model(body.get('whisper_model', 'base') if 'body' in locals() else request.POST.get('whisper_model', 'base'))
        except:
            signature = request.POST.get('signature', '')
            password_phrase = request.POST.get('password_phrase', '')
            extract_screenshots = request.POST.get('extract_screenshots') == 'on'
            whisper_model, _ = validate_whisper_model(request.POST.get('whisper_model', 'base'))
        
        password_phrase_hash = None
        if password_phrase:
            password_phrase_hash = Transcription.hash_password_phrase(password_phrase)
        
        upload_session = str(uuid.uuid4())
        transcription_ids = []
        
        for url in urls:
            url = url.strip()
            if not url:
                continue
            
            try:
                temp_file_path, filename = download_from_url(url)
                file_size = os.path.getsize(temp_file_path)
                if file_size == 0:
                    os.unlink(temp_file_path)
                    continue
                
                uploads_base_dir = os.path.join(settings.MEDIA_ROOT, 'uploads')
                os.makedirs(uploads_base_dir, exist_ok=True)
                file_uuid = str(uuid.uuid4())
                uploads_dir = os.path.join(uploads_base_dir, file_uuid)
                os.makedirs(uploads_dir, exist_ok=True)
                file_ext = os.path.splitext(filename)[1]
                original_filename = f"original{file_ext}"
                original_file_path = os.path.join(uploads_dir, original_filename)
                shutil.copy2(temp_file_path, original_file_path)
                os.unlink(temp_file_path)
                
                transcription = Transcription.objects.create(
                    filename=filename,
                    ip_address=ip_address,
                    user_uuid=user_uuid,
                    signature=signature,
                    password_phrase_hash=password_phrase_hash,
                    file_size=file_size,
                    extract_screenshots=extract_screenshots,
                    whisper_model=whisper_model,
                    status='pending',
                    upload_session=upload_session,
                    original_file_path=original_file_path
                )
                
                transcription_ids.append(transcription.id)
                ip_counter.increment_upload()
                uuid_counter.increment_upload()
                
                thread = threading.Thread(target=process_file, args=(transcription.id, original_file_path))
                thread.daemon = True
                thread.start()
                
            except Exception as e:
                logger.error(f"Ошибка при обработке URL {url}: {e}", exc_info=True)
                continue
        
        if not transcription_ids:
            return JsonResponse({'error': 'Не удалось загрузить ни один файл'}, status=400)
        
        return JsonResponse({
            'success': True,
            'transcription_ids': transcription_ids,
            'upload_session': upload_session,
            'message': f'Загружено файлов: {len(transcription_ids)}'
        })
        
    except Exception as e:
        logger.error(f"Ошибка при загрузке по URL: {e}", exc_info=True)
        return JsonResponse({'error': f'Ошибка при загрузке: {str(e)}'}, status=500)
