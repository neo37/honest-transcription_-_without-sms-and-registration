import os
import threading
import subprocess
import logging
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from .models import Transcription
from faster_whisper import WhisperModel
import tempfile
import shutil

logger = logging.getLogger(__name__)


# Глобальная переменная для модели Whisper (загружается один раз)
whisper_model = None
model_lock = threading.Lock()


def get_whisper_model():
    """Получить или загрузить модель Whisper"""
    import logging
    logger = logging.getLogger(__name__)
    
    global whisper_model
    if whisper_model is None:
        with model_lock:
            if whisper_model is None:
                # Используем базовую модель для скорости (можно изменить на base, small, medium, large)
                # Для слабого сервера используем base или small
                # compute_type="int8" - быстрее, но менее точно
                # compute_type="float16" - медленнее, но точнее (если есть GPU)
                logger.info("Загрузка модели Whisper...")
                whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
                logger.info("Модель Whisper загружена")
    return whisper_model


def index(request):
    """Главная страница с формой загрузки и списком транскрипций"""
    import shutil
    
    # Получаем информацию о диске
    total, used, free = shutil.disk_usage('/')
    disk_info = {
        'total_gb': total / (1024**3),
        'used_gb': used / (1024**3),
        'free_gb': free / (1024**3),
        'used_percent': (used / total) * 100
    }
    
    # Проверяем, есть ли активная фраза-пароль в сессии
    active_password_phrase = request.session.get('password_phrase', None)
    
    if active_password_phrase:
        # Фильтруем транскрипции по фразе-паролю
        password_hash = Transcription.hash_password_phrase(active_password_phrase)
        transcriptions = Transcription.objects.filter(
            password_phrase_hash=password_hash
        ).order_by('-uploaded_at')[:50]
    else:
        # Показываем только транскрипции без пароля
        # Показываем только последние 2 без пароля, остальные скрыты
        all_no_password = Transcription.objects.filter(
            password_phrase_hash__isnull=True
        ).order_by('-uploaded_at')
        
        # Берем последние 2 для отображения
        transcriptions = all_no_password[:2]
        
        # Удаляем старые файлы без пароля (оставляем только последние 2)
        # Файлы уже удалены после обработки, но транскрипции остаются в БД
    
    return render(request, 'transcribe/index.html', {
        'transcriptions': transcriptions,
        'is_logged_in': active_password_phrase is not None,
        'active_phrase': active_password_phrase,
        'disk_info': disk_info
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
    ip_address = get_client_ip(request)
    
    # Получаем подпись (если есть)
    signature = request.POST.get('signature', '').strip()
    
    # Получаем фразу-пароль (если есть)
    password_phrase = request.POST.get('password_phrase', '').strip()
    password_phrase_hash = None
    if password_phrase:
        password_phrase_hash = Transcription.hash_password_phrase(password_phrase)
    
    # Получаем флаг извлечения скриншотов
    extract_screenshots = request.POST.get('extract_screenshots') == 'on'
    
    # Получаем выбранную модель Whisper
    whisper_model = request.POST.get('whisper_model', 'base')
    if whisper_model not in [choice[0] for choice in Transcription.WHISPER_MODELS]:
        whisper_model = 'base'
    
    # Создаем уникальную сессию загрузки для группировки файлов
    import uuid
    upload_session = str(uuid.uuid4())
    
    # Проверка размера файлов (500 МБ = 500 * 1024 * 1024 байт)
    max_size = 500 * 1024 * 1024
    transcription_ids = []
    
    for uploaded_file in uploaded_files:
        if uploaded_file.size > max_size:
            return JsonResponse({'error': f'Файл {uploaded_file.name} слишком большой. Максимальный размер: 500 МБ'}, status=400)
        
        # Сохраняем файл во временную директорию сразу
        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploaded_file.name)[1]) as temp_file:
                temp_file_path = temp_file.name
                shutil.copyfileobj(uploaded_file, temp_file)
        except Exception as e:
            return JsonResponse({'error': f'Ошибка при сохранении файла {uploaded_file.name}: {str(e)}'}, status=500)
        
        # Создаем запись в БД
        transcription = Transcription.objects.create(
            filename=uploaded_file.name,
            ip_address=ip_address,
            signature=signature if signature else None,
            password_phrase_hash=password_phrase_hash,
            file_size=uploaded_file.size,
            extract_screenshots=extract_screenshots,
            upload_session=upload_session,
            whisper_model=whisper_model,
            status='pending'
        )
        
        # Генерируем публичный токен сразу
        transcription.generate_public_token()
        
        transcription_ids.append(transcription.id)
        
        # Запускаем обработку в отдельном потоке
        thread = threading.Thread(target=process_file, args=(transcription.id, temp_file_path))
        thread.daemon = True
        thread.start()
    
    return JsonResponse({
        'success': True,
        'transcription_ids': transcription_ids,
        'upload_session': upload_session,
        'count': len(transcription_ids),
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
    """Извлекает скриншоты из видео (1 раз в минуту)"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Получаем длительность видео
        ffmpeg_path = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
        
        # Используем ffprobe для получения длительности
        cmd_probe = [
            ffmpeg_path.replace('ffmpeg', 'ffprobe') if 'ffmpeg' in ffmpeg_path else 'ffprobe',
            '-i', video_path,
            '-show_entries', 'format=duration',
            '-v', 'quiet',
            '-of', 'csv=p=0'
        ]
        
        result_probe = subprocess.run(cmd_probe, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        
        if result_probe.returncode == 0:
            try:
                duration_seconds = float(result_probe.stdout.decode('utf-8').strip())
            except:
                logger.warning("Не удалось распарсить длительность видео")
                return []
        else:
            # Альтернативный способ через ffmpeg
            cmd_duration = [
                ffmpeg_path,
                '-i', video_path,
                '-hide_banner',
                '-loglevel', 'error',
                '-f', 'null',
                '-'
            ]
            result = subprocess.run(cmd_duration, stderr=subprocess.PIPE, timeout=30)
            
            if result.stderr:
                import re
                duration_pattern = r'Duration: (\d{2}):(\d{2}):(\d{2})\.\d{2}'
                match = re.search(duration_pattern, result.stderr.decode('utf-8', errors='ignore'))
                if match:
                    hours, minutes, seconds = map(int, match.groups())
                    duration_seconds = hours * 3600 + minutes * 60 + seconds
                else:
                    logger.warning("Не удалось определить длительность видео")
                    return []
            else:
                logger.warning("Не удалось определить длительность видео")
                return []
        
        # Создаем директорию для скриншотов
        os.makedirs(output_dir, exist_ok=True)
        
        # Извлекаем скриншоты каждую минуту (60 секунд)
        screenshots = []
        timestamp = 0
        order = 0
        
        while timestamp < duration_seconds:
            screenshot_path = os.path.join(output_dir, f"screenshot_{order:04d}.jpg")
            
            cmd_screenshot = [
                ffmpeg_path,
                '-i', video_path,
                '-ss', str(timestamp),
                '-vframes', '1',
                '-q:v', '2',  # Качество JPEG (2 = высокое)
                '-y',
                screenshot_path
            ]
            
            result = subprocess.run(
                cmd_screenshot,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30
            )
            
            if result.returncode == 0 and os.path.exists(screenshot_path):
                # Сохраняем относительный путь от MEDIA_ROOT
                # Убираем полный путь и оставляем только путь относительно media
                if screenshot_path.startswith(str(settings.MEDIA_ROOT)):
                    relative_path = os.path.relpath(screenshot_path, settings.MEDIA_ROOT)
                else:
                    # Если путь уже относительный или другой формат
                    relative_path = screenshot_path.replace(str(settings.MEDIA_ROOT) + '/', '').replace('/root/media/', '')
                
                # Сохраняем в БД
                from .models import Screenshot
                screenshot = Screenshot.objects.create(
                    transcription_id=transcription_id,
                    timestamp=timestamp,
                    image_path=relative_path,
                    order=order
                )
                screenshots.append(screenshot)
                logger.info(f"Скриншот извлечен: {timestamp:.0f}s -> {relative_path}")
            
            timestamp += 60  # Следующая минута
            order += 1
        
        logger.info(f"Извлечено {len(screenshots)} скриншотов из видео")
        return screenshots
        
    except Exception as e:
        logger.error(f"Ошибка при извлечении скриншотов: {e}", exc_info=True)
        return []


def process_file(transcription_id, temp_file_path):
    """Обработка файла в фоновом режиме"""
    import logging
    logger = logging.getLogger(__name__)
    
    transcription = Transcription.objects.get(id=transcription_id)
    transcription.status = 'processing'
    transcription.save()
    
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
                screenshots_dir = os.path.join(settings.MEDIA_ROOT, 'screenshots', str(transcription_id))
                extract_screenshots_from_video(temp_file_path, transcription_id, screenshots_dir)
        
        # Извлекаем аудио дорожку в отдельный файл
        # Это гарантирует, что мы транскрибируем именно аудио, а не субтитры
        audio_file_path = temp_file_path + "_audio.wav"
        extract_audio(temp_file_path, audio_file_path)
        
        if not os.path.exists(audio_file_path) or os.path.getsize(audio_file_path) == 0:
            raise Exception("Не удалось извлечь аудио дорожку из файла")
        
        # Транскрибируем файл используя выбранную модель
        model_name = transcription.whisper_model or 'base'
        model = get_whisper_model(model_name)
        
        # Используем более точные параметры для транскрибации
        # task="transcribe" - транскрибировать речь (не переводить)
        # language=None - автоопределение языка
        # beam_size=5 - баланс между скоростью и качеством
        # vad_filter=True - фильтр голосовой активности для лучшего качества
        segments, info = model.transcribe(
            audio_file_path,
            beam_size=5,
            language=None,  # Автоопределение языка
            task="transcribe",
            vad_filter=True,  # Фильтр голосовой активности
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        
        # Собираем текст из сегментов
        text_parts = []
        segment_count = 0
        for segment in segments:
            text = segment.text.strip()
            if text:  # Пропускаем пустые сегменты
                text_parts.append(text)
                segment_count += 1
        
        transcribed_text = " ".join(text_parts).strip()
        
        # Если текст пустой или слишком короткий, это может быть ошибка
        if not transcribed_text or len(transcribed_text) < 10:
            raise Exception(f"Транскрибация вернула пустой или слишком короткий текст. Сегментов обработано: {segment_count}")
        
        # Обновляем запись
        transcription.transcribed_text = transcribed_text
        transcription.status = 'completed'
        transcription.save()
        
        logger.info(f"Транскрибация завершена для файла {transcription.filename}. Сегментов: {segment_count}, Длина текста: {len(transcribed_text)}")
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        transcription.status = 'error'
        transcription.error_message = error_msg
        transcription.save()
        logger.error(f"Ошибка при обработке файла {transcription.filename}: {error_msg}", exc_info=True)
    finally:
        # Удаляем временные файлы (но не скриншоты)
        for file_path in [temp_file_path, audio_file_path]:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Ошибка при удалении временного файла {file_path}: {e}")


def get_client_ip(request):
    """Получить IP адрес клиента"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


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
        
        return JsonResponse({
            'status': transcription.status,
            'text': transcription.transcribed_text if transcription.status == 'completed' else None,
            'error': transcription.error_message if transcription.status == 'error' else None
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
            'MEDIA_URL': settings.MEDIA_URL
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
                image_path = os.path.join(settings.MEDIA_ROOT, screenshot.image_path)
                if os.path.exists(image_path):
                    zip_file.write(image_path, f"screenshot_{screenshot.order:04d}_{screenshot.timestamp:.0f}s.jpg")
        
        zip_buffer.seek(0)
        response = HttpResponse(zip_buffer.read(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{transcription.filename}_screenshots.zip"'
        return response
    except Transcription.DoesNotExist:
        return HttpResponse("Транскрипция не найдена", status=404)


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

