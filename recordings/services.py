import logging
import re
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from django.db import transaction

from .models import Recording, PollLog, Space, AIConfig
from .s3_client import list_mp3_files, download_mp3_to_local
from .queue_services import enqueue_transcribe, enqueue_embedding, get_next_transcribe_task, get_next_embedding_task, index_embedding_for_recording

logger = logging.getLogger(__name__)


def _extract_space_slug(s3_key: str):
    """Ищет паттерн -org-{slug} в конце ключа S3."""
    m = re.search(r'-org-([a-z0-9\-]+?)(?:\.mp3)?$', s3_key, re.IGNORECASE)
    return m.group(1) if m else None


def reset_hung_transcriptions():
    """Сбросить записи, зависшие в TRANSCRIBING дольше 2 часов."""
    from .queue_services import enqueue_transcribe
    threshold = timezone.now() - timezone.timedelta(hours=2)
    hung = Recording.objects.filter(status=Recording.Status.TRANSCRIBING, updated_at__lt=threshold)
    for rec in hung:
        logger.warning('Resetting hung transcription: id=%s %s', rec.pk, rec.filename)
        rec.status = Recording.Status.STABLE
        rec.transcription_progress = 0
        rec.transcription_stage = ''
        rec.error_message = 'Перезапуск: процесс был прерван'
        rec.save(update_fields=['status', 'transcription_progress', 'transcription_stage', 'error_message'])
        enqueue_transcribe(rec, priority=0)


def run_poll_and_transcribe():
    """
    Один цикл: опрос S3, обновление стабильности, добавление стабильных в очередь.
    Транскрибация выполняется воркером из очереди (run_poller обрабатывает очередь).
    """
    reset_hung_transcriptions()
    log = PollLog.objects.create(success=True)
    try:
        files = list_mp3_files()
        log.files_found = len(files)
        log.save()

        now = timezone.now()
        stable_delta = timezone.timedelta(seconds=settings.STABLE_SIZE_SECONDS)

        for item in files:
            key = item['key']
            size = item['size']
            filename = key.split('/')[-1] if '/' in key else key

            rec, created = Recording.objects.get_or_create(
                s3_key=key,
                defaults={
                    'filename': filename,
                    'size_bytes': size,
                    'last_size_check_at': now,
                    'size_stable_since': None,
                    'status': Recording.Status.PENDING,
                },
            )
            if created:
                slug = _extract_space_slug(key)
                if slug:
                    space = Space.objects.filter(slug=slug).first()
                else:
                    space = Space.objects.filter(slug=getattr(settings, 'BP_SPACE_SLUG', 'org-bp')).first()
                if space:
                    rec.space = space
                    rec.save(update_fields=['space'])
                continue

            if rec.status in (Recording.Status.DONE, Recording.Status.FAILED, Recording.Status.TRANSCRIBING):
                continue

            new_size = size
            rec.last_size_check_at = now

            if new_size != rec.size_bytes:
                rec.size_bytes = new_size
                rec.size_stable_since = None
            else:
                if rec.size_stable_since is None:
                    rec.size_stable_since = now
                if (now - rec.size_stable_since) >= stable_delta:
                    rec.status = Recording.Status.STABLE
                    log.files_stable += 1
                    enqueue_transcribe(rec, priority=0)

            rec.save()

        log.save()
        log.finished_at = timezone.now()
        log.message = f'Найдено {log.files_found}, стабильных {log.files_stable}'
        log.save()
        return log
    except Exception as e:
        logger.exception('Poll failed')
        log.success = False
        log.message = str(e)
        log.finished_at = timezone.now()
        log.save()
        raise


def _set_progress(rec: 'Recording', progress: int, stage: str):
    rec.transcription_progress = progress
    rec.transcription_stage = stage
    # Используем queryset update чтобы обновить updated_at (auto_now не работает с update_fields)
    Recording.objects.filter(pk=rec.pk).update(
        transcription_progress=progress,
        transcription_stage=stage,
        updated_at=timezone.now(),
    )


def _cosine_similarity(a, b):
    import numpy as np
    a, b = np.array(a, dtype=float), np.array(b, dtype=float)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom < 1e-8:
        return 0.0
    return float(np.dot(a, b) / denom)


def _iter_diarize_segments(diarize_segments):
    """Унифицированный итератор по сегментам диаризации.
    whisperx возвращает pandas DataFrame, pyannote — Annotation.
    Yields: (speaker_id, start_sec, end_sec)
    """
    try:
        import pandas as pd
        if isinstance(diarize_segments, pd.DataFrame):
            for _, row in diarize_segments.iterrows():
                spk = row.get('speaker', None) or str(row.iloc[2])
                start = float(row.get('start', 0))
                end = float(row.get('end', 0))
                yield spk, start, end
            return
    except ImportError:
        pass
    # pyannote Annotation
    for turn, _, speaker in diarize_segments.itertracks(yield_label=True):
        yield speaker, turn.start, turn.end


def _extract_speaker_embeddings(audio, diarize_segments, diarize_model):
    """Извлечь усреднённые эмбеддинги спикеров из аудио через модель диаризации."""
    import torch
    import numpy as np

    try:
        embedding_inference = diarize_model.model._embedding
    except AttributeError:
        logger.warning('Cannot access diarize_model._embedding, skipping embeddings')
        return {}

    sample_rate = 16000
    speaker_segments = {}
    for speaker, start, end in _iter_diarize_segments(diarize_segments):
        if end - start < 1.0:
            continue
        speaker_segments.setdefault(speaker, []).append((start, end))

    speaker_embeddings = {}
    for speaker, segs in speaker_segments.items():
        embs = []
        for start, end in segs:
            s = int(start * sample_rate)
            e = min(int(end * sample_rate), len(audio))
            if e - s < sample_rate:
                continue
            chunk = torch.tensor(audio[s:e], dtype=torch.float32).unsqueeze(0)
            try:
                with torch.no_grad():
                    emb = embedding_inference({'waveform': chunk, 'sample_rate': sample_rate})
                if emb is not None:
                    embs.append(np.array(emb).squeeze())
            except Exception:
                continue
        if embs:
            speaker_embeddings[speaker] = np.mean(embs, axis=0).tolist()

    return speaker_embeddings


def match_speaker_profiles(space, speaker_embeddings, threshold=0.82):
    """Сопоставить эмбеддинги спикеров с сохранёнными профилями. Возвращает {speaker_id: name}."""
    from .models import SpeakerProfile
    if not speaker_embeddings:
        return {}
    profiles = list(SpeakerProfile.objects.filter(space=space).exclude(embedding=[]))
    if not profiles:
        return {}

    matched = {}
    for speaker_id, emb in speaker_embeddings.items():
        best_name, best_sim = None, threshold
        for profile in profiles:
            sim = _cosine_similarity(emb, profile.embedding)
            if sim > best_sim:
                best_sim = sim
                best_name = profile.name
        if best_name:
            matched[speaker_id] = best_name
            logger.info('Speaker %s matched profile "%s" (sim=%.3f)', speaker_id, best_name, best_sim)

    return matched


def _extract_speech_patterns(transcription_text: str, speaker_id: str) -> dict:
    """
    Извлечь речевые паттерны спикера из транскрипции — топ слов и биграмм.
    Игнорирует стоп-слова. Возвращает {'top_words': {word: count}, 'phrases': {bigram: count}, 'sample_words': N}
    """
    import re
    from collections import Counter

    STOP_WORDS = {
        'и', 'в', 'не', 'на', 'я', 'что', 'с', 'он', 'как', 'это', 'но', 'они',
        'мы', 'к', 'из', 'по', 'же', 'а', 'то', 'от', 'за', 'о', 'для', 'до',
        'у', 'вот', 'так', 'да', 'ну', 'вы', 'было', 'есть', 'нет', 'ещё', 'уже',
        'всё', 'если', 'при', 'тоже', 'или', 'бы', 'со', 'всего', 'все', 'он',
        'его', 'её', 'им', 'их', 'мне', 'тут', 'там', 'был', 'она', 'когда', 'тебя',
        'чтобы', 'будет', 'себя', 'этот', 'этого', 'эту', 'эти', 'этой',
    }

    # Собираем только реплики этого спикера
    lines = transcription_text.split('\n')
    words = []
    for line in lines:
        m = re.match(r'^[\-—]\s*(' + re.escape(speaker_id) + r'):\s*(.*)', line)
        if m:
            text = m.group(2).lower()
            tokens = re.findall(r'[а-яёa-z]{3,}', text)
            filtered = [t for t in tokens if t not in STOP_WORDS]
            words.extend(filtered)

    if len(words) < 10:
        return {}

    word_counter = Counter(words)
    top_words = dict(word_counter.most_common(30))

    # Биграммы (пары слов подряд)
    bigrams = [f'{words[i]} {words[i+1]}' for i in range(len(words) - 1)]
    bigram_counter = Counter(bigrams)
    top_phrases = {k: v for k, v in bigram_counter.most_common(20) if v >= 2}

    return {
        'top_words': top_words,
        'phrases': top_phrases,
        'sample_words': len(words),
    }


def _merge_speech_patterns(existing: dict, new: dict, weight_existing: int = 1) -> dict:
    """Слить два словаря паттернов с взвешенным усреднением."""
    if not existing:
        return new
    if not new:
        return existing

    def merge_freq(old_d, new_d, w):
        merged = dict(old_d)
        for k, v in new_d.items():
            merged[k] = merged.get(k, 0) + v
        # нормализуем
        total = sum(merged.values()) or 1
        return {k: round(v * 100 / total, 2) for k, v in sorted(merged.items(), key=lambda x: -x[1])[:30]}

    return {
        'top_words': merge_freq(existing.get('top_words', {}), new.get('top_words', {}), weight_existing),
        'phrases': merge_freq(existing.get('phrases', {}), new.get('phrases', {}), weight_existing),
        'sample_words': existing.get('sample_words', 0) + new.get('sample_words', 0),
    }


def _speech_pattern_similarity(profile_patterns: dict, candidate_patterns: dict) -> float:
    """
    Сходство речевых паттернов: Jaccard по топ-словам + пересечение биграмм.
    Возвращает 0.0–1.0.
    """
    if not profile_patterns or not candidate_patterns:
        return 0.0

    p_words = set(profile_patterns.get('top_words', {}).keys())
    c_words = set(candidate_patterns.get('top_words', {}).keys())
    if not p_words or not c_words:
        return 0.0

    # Jaccard similarity на топ-словах
    intersection = len(p_words & c_words)
    union = len(p_words | c_words)
    word_sim = intersection / union if union else 0.0

    # Пересечение биграмм
    p_phrases = set(profile_patterns.get('phrases', {}).keys())
    c_phrases = set(candidate_patterns.get('phrases', {}).keys())
    if p_phrases and c_phrases:
        phrase_union = len(p_phrases | c_phrases)
        phrase_sim = len(p_phrases & c_phrases) / phrase_union if phrase_union else 0.0
    else:
        phrase_sim = 0.0

    return word_sim * 0.6 + phrase_sim * 0.4


def match_by_speech_patterns(space, transcription_text: str, speaker_ids: list, threshold=0.30) -> dict:
    """
    Сопоставить спикеров по речевым паттернам с профилями в БД.
    Используется как дополнение к аудиоэмбеддингам.
    Возвращает {speaker_id: name}.
    """
    from .models import SpeakerProfile
    profiles = list(SpeakerProfile.objects.filter(space=space).exclude(speech_patterns={}))
    if not profiles:
        return {}

    matched = {}
    already_used = set()  # один профиль — один спикер

    # Вычислим паттерны для каждого спикера
    candidate_patterns = {}
    for sid in speaker_ids:
        p = _extract_speech_patterns(transcription_text, sid)
        if p:
            candidate_patterns[sid] = p

    # Для каждого спикера найдём лучший профиль
    scores = []
    for sid, cand in candidate_patterns.items():
        for profile in profiles:
            sim = _speech_pattern_similarity(profile.speech_patterns, cand)
            if sim >= threshold:
                scores.append((sim, sid, profile.name))

    # Жадный матчинг: сначала самые похожие
    scores.sort(reverse=True)
    for sim, sid, name in scores:
        if sid not in matched and name not in already_used:
            matched[sid] = name
            already_used.add(name)
            logger.info('Speaker %s matched by speech patterns to "%s" (sim=%.3f)', sid, name, sim)

    return matched


def save_speaker_profiles(space, speaker_names, speaker_embeddings, transcription_text=''):
    """Обновить или создать профили спикеров по назначенным именам, эмбеддингам и речевым паттернам."""
    from .models import SpeakerProfile
    import numpy as np

    for speaker_id, name in speaker_names.items():
        if not name or not name.strip():
            continue

        emb = speaker_embeddings.get(speaker_id) if speaker_embeddings else None
        new_patterns = _extract_speech_patterns(transcription_text, speaker_id) if transcription_text else {}

        if not emb and not new_patterns:
            continue

        profile = SpeakerProfile.objects.filter(space=space, name=name).first()
        if profile:
            if emb:
                if profile.embedding:
                    old = np.array(profile.embedding, dtype=float)
                    new_e = np.array(emb, dtype=float)
                    n = profile.sample_count
                    averaged = (old * n + new_e) / (n + 1)
                    profile.embedding = averaged.tolist()
                else:
                    profile.embedding = emb
            if new_patterns:
                profile.speech_patterns = _merge_speech_patterns(
                    profile.speech_patterns, new_patterns, profile.sample_count
                )
            profile.sample_count += 1
            profile.save()
        else:
            SpeakerProfile.objects.create(
                space=space, name=name,
                embedding=emb or [],
                speech_patterns=new_patterns,
                sample_count=1,
            )
        logger.info('Saved speaker profile "%s" for space %s', name, space)


def _transcribe_whisperx(local_path: Path, rec=None) -> str:
    """
    Транскрибация через WhisperX с диаризацией спикеров.
    Использует GPU если доступен (быстрее в 5-10x), иначе CPU.
    Формат вывода:
      — Участник 1: Добрый день, коллеги...
      — Участник 2: Привет, начнём совещание.
    """
    import torch
    import whisperx

    class _DiarizationWrapper:
        """Direct pyannote wrapper compatible with whisperx.assign_word_speakers.

        whisperx>=3.8 expects output.speaker_diarization (community-1 only),
        but pyannote/speaker-diarization-3.1 returns Annotation directly.
        This wrapper handles both and keeps .model exposed for embeddings.
        """
        def __init__(self, model_name, token, device):
            from pyannote.audio import Pipeline
            self.model = Pipeline.from_pretrained(model_name, token=token).to(torch.device(device))

        def __call__(self, audio, min_speakers=None, max_speakers=None, num_speakers=None):
            import pandas as pd
            from whisperx.audio import SAMPLE_RATE
            audio_data = {'waveform': torch.from_numpy(audio[None, :]), 'sample_rate': SAMPLE_RATE}
            kwargs = {}
            if num_speakers is not None:
                kwargs['num_speakers'] = num_speakers
            if min_speakers is not None:
                kwargs['min_speakers'] = min_speakers
            if max_speakers is not None:
                kwargs['max_speakers'] = max_speakers
            output = self.model(audio_data, **kwargs)
            # speaker-diarization-3.1 returns Annotation directly;
            # community-1 wraps it in an object with .speaker_diarization
            diarization = getattr(output, 'speaker_diarization', output)
            diarize_df = pd.DataFrame(
                diarization.itertracks(yield_label=True),
                columns=['segment', 'label', 'speaker'],
            )
            diarize_df['start'] = diarize_df['segment'].apply(lambda x: x.start)
            diarize_df['end'] = diarize_df['segment'].apply(lambda x: x.end)
            return diarize_df

    # Определяем устройство: приоритет — per-recording override, затем ocr_gpu_mode, затем авто
    from .models import SystemConfig
    device_override_key = f'device_override_{rec.pk}' if rec else None
    device_override = SystemConfig.get(device_override_key, '') if device_override_key else ''
    if device_override_key and device_override:
        SystemConfig.set(device_override_key, '')  # сразу очищаем
    if device_override == 'cpu':
        device = "cpu"
        logger.info("WhisperX: принудительно CPU (override)")
    elif device_override == 'gpu':
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("WhisperX: принудительно GPU (override) → %s", device)
    else:
        ocr_gpu_mode = SystemConfig.get('ocr_gpu_mode', '0') == '1'
        if ocr_gpu_mode:
            device = "cpu"
            logger.info("WhisperX: GPU занят OCR-режимом, используем CPU")
        else:
            device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    logger.info("WhisperX device: %s, compute_type: %s", device, compute_type)
    hf_token = settings.HUGGINGFACE_TOKEN

    # 1. Загружаем аудио
    if rec:
        _set_progress(rec, 10, 'Загрузка аудио')
    audio = whisperx.load_audio(str(local_path))

    # 2. Транскрипция — модель small (баланс качества и RAM на CPU)
    if rec:
        _set_progress(rec, 25, 'Транскрипция речи')

    def _patch_pyannote_meta_tensors():
        """
        Фикс несовместимости pyannote 4.x + torch 2.8+:
        при загрузке checkpoint lightning создаёт meta-тензоры, а
        module.to(device) на них падает с NotImplementedError.
        Патчим Model.setup чтобы сначала материализовать meta-параметры через to_empty().
        """
        try:
            from pyannote.audio.core import model as _pm
            if getattr(_pm.Model, '_meta_patch_applied', False):
                return
            _orig_setup = _pm.Model.setup

            def _safe_setup(self, stage=None):
                target_device = str(getattr(self, 'device', 'cpu'))
                for module in self.modules():
                    for name, param in list(module._parameters.items()):
                        if param is not None and param.is_meta:
                            module._parameters[name] = torch.nn.Parameter(
                                torch.empty(param.shape, dtype=param.dtype, device=target_device),
                                requires_grad=param.requires_grad,
                            )
                    for name, buf in list(module._buffers.items()):
                        if buf is not None and buf.is_meta:
                            module._buffers[name] = torch.empty(
                                buf.shape, dtype=buf.dtype, device=target_device,
                            )
                return _orig_setup(self, stage)

            _pm.Model.setup = _safe_setup
            _pm.Model._meta_patch_applied = True
        except Exception as _e:
            logger.warning('pyannote meta-tensor patch failed: %s', _e)

    _patch_pyannote_meta_tensors()

    def _load_whisper(dev, ctype):
        m = whisperx.load_model("small", device=dev, compute_type=ctype, language="ru")
        # Уменьшаем batch_size у внутренней VAD-модели pyannote (default=32, OOM на 2GB)
        try:
            m.vad_model.vad_pipeline._inferences['_segmentation'].batch_size = 2
        except Exception:
            try:
                m.vad_model._segmentation.batch_size = 2  # старый путь
            except Exception:
                pass
        return m

    try:
        model = _load_whisper(device, compute_type)
        batch_size = 4 if device == "cuda" else 4
        result = model.transcribe(audio, language="ru", batch_size=batch_size)
    except (torch.cuda.OutOfMemoryError, MemoryError, RuntimeError) as e:
        logger.warning("GPU ошибка при транскрипции (%s), падаем на CPU", e)
        if device == "cuda":
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
        device = "cpu"
        compute_type = "int8"
        model = _load_whisper(device, compute_type)
        result = model.transcribe(audio, language="ru", batch_size=4)
    del model

    # 3. Выравнивание слов по времени (нужно для диаризации)
    if rec:
        _set_progress(rec, 55, 'Выравнивание по времени')
    try:
        align_model, metadata = whisperx.load_align_model(language_code="ru", device=device)
        result = whisperx.align(result["segments"], align_model, metadata, audio, device)
    except (torch.cuda.OutOfMemoryError, MemoryError, RuntimeError):
        logger.warning("GPU ошибка при align, падаем на CPU")
        if device == "cuda":
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
        device = "cpu"
        align_model, metadata = whisperx.load_align_model(language_code="ru", device=device)
        result = whisperx.align(result["segments"], align_model, metadata, audio, device)
    del align_model

    # 4. Диаризация — кто говорит
    if rec:
        _set_progress(rec, 70, 'Определение спикеров')
    try:
        diarize_model = _DiarizationWrapper(
            model_name="pyannote/speaker-diarization-3.1",
            token=hf_token,
            device=device,
        )
        diarize_segments = diarize_model(audio, min_speakers=1, max_speakers=10)
    except (torch.cuda.OutOfMemoryError, MemoryError, RuntimeError):
        logger.warning("GPU ошибка при диаризации, падаем на CPU")
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        device = "cpu"
        diarize_model = _DiarizationWrapper(
            model_name="pyannote/speaker-diarization-3.1",
            token=hf_token,
            device=device,
        )
        diarize_segments = diarize_model(audio, min_speakers=1, max_speakers=10)

    # 5. Назначаем спикеров сегментам
    if rec:
        _set_progress(rec, 90, 'Сборка диалога')
    result = whisperx.assign_word_speakers(diarize_segments, result)

    # 6. Извлекаем эмбеддинги спикеров для профилей
    speaker_embeddings = _extract_speaker_embeddings(audio, diarize_segments, diarize_model)
    del diarize_model
    # 6. Формируем диалог: объединяем соседние реплики одного спикера
    # Нумеруем спикеров: SPEAKER_00 → Участник 1 и т.д.
    speaker_map = {}
    counter = [0]

    def get_name(raw):
        if raw not in speaker_map:
            counter[0] += 1
            speaker_map[raw] = f"Участник {counter[0]}"
        return speaker_map[raw]

    lines = []
    current_speaker = None
    current_text = []

    for seg in result["segments"]:
        raw = seg.get("speaker") or "SPEAKER_00"
        name = get_name(raw)
        text = seg.get("text", "").strip()
        if not text:
            continue
        if name != current_speaker:
            if current_text:
                lines.append(f"— {current_speaker}: {' '.join(current_text)}")
            current_speaker = name
            current_text = [text]
        else:
            current_text.append(text)

    if current_text:
        lines.append(f"— {current_speaker}: {' '.join(current_text)}")

    # Переименовываем ключи эмбеддингов: SPEAKER_00 → Участник 1 (как в тексте)
    if speaker_embeddings and speaker_map:
        speaker_embeddings = {speaker_map.get(k, k): v for k, v in speaker_embeddings.items()}
    if rec and speaker_embeddings:
        rec.speaker_embeddings = speaker_embeddings
        rec.save(update_fields=['speaker_embeddings'])

    return '\n'.join(lines) if lines else ''


def _transcribe_faster_whisper(local_path: Path) -> str:
    """Локальная транскрибация через faster-whisper (без диаризации)."""
    import torch
    from faster_whisper import WhisperModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    try:
        model = WhisperModel("small", device=device, compute_type=compute_type)
    except RuntimeError:
        device = "cpu"
        compute_type = "int8"
        model = WhisperModel("small", device=device, compute_type=compute_type)
    segments, info = model.transcribe(str(local_path), language="ru", beam_size=5)
    return ' '.join(s.text for s in segments).strip()


def transcribe_one(rec: Recording):
    """Скачать MP3, выполнить транскрипцию с диаризацией (whisperx) или без (faster-whisper)."""
    rec.status = Recording.Status.TRANSCRIBING
    rec.error_message = ''
    rec.transcription_progress = 5
    rec.transcription_stage = 'Скачивание файла'
    Recording.objects.filter(pk=rec.pk).update(
        status=Recording.Status.TRANSCRIBING,
        error_message='',
        transcription_progress=5,
        transcription_stage='Скачивание файла',
        updated_at=timezone.now(),
    )

    download_dir = Path(settings.RECORDINGS_DOWNLOAD_DIR)
    download_dir.mkdir(parents=True, exist_ok=True)
    safe_name = rec.s3_key.replace('/', '_')
    local_path = download_dir / safe_name

    try:
        download_mp3_to_local(rec.s3_key, local_path)
        if settings.HUGGINGFACE_TOKEN:
            text = _transcribe_whisperx(local_path, rec=rec)
        else:
            _set_progress(rec, 30, 'Транскрипция речи')
            text = _transcribe_faster_whisper(local_path)
        rec.transcription = text
        rec.transcribed_at = timezone.now()
        rec.status = Recording.Status.DONE
        rec.error_message = ''
        rec.transcription_progress = 100
        rec.transcription_stage = 'Готово'
        # Auto-match speaker names from profiles (аудиоэмбеддинги + речевые паттерны)
        if rec.space:
            auto_names = {}
            # 1. Сначала по аудиоэмбеддингам (точнее)
            if rec.speaker_embeddings and settings.HUGGINGFACE_TOKEN:
                auto_names = match_speaker_profiles(rec.space, rec.speaker_embeddings)
            # 2. Добавляем незаполненных — по речевым паттернам
            all_speaker_ids = list(rec.speaker_embeddings.keys()) if rec.speaker_embeddings else []
            unmatched = [sid for sid in all_speaker_ids if sid not in auto_names]
            if unmatched and text:
                pattern_names = match_by_speech_patterns(rec.space, text, unmatched)
                auto_names.update(pattern_names)
            if auto_names:
                rec.speaker_names = auto_names
    except Exception as e:
        logger.exception('Transcribe failed for %s', rec.s3_key)
        rec.status = Recording.Status.FAILED
        rec.error_message = str(e)
        rec.transcription_progress = 0
        rec.transcription_stage = ''
    finally:
        rec.save()
        if local_path.exists():
            try:
                local_path.unlink()
            except OSError:
                pass
        if rec.status == Recording.Status.DONE:
            generate_ai_summary(rec)
            enqueue_embedding(rec)


def call_llm_api(prompt: str, session_id: str, log_id: str, timeout: int = 120) -> str:
    """
    Вызов LLM API с настройками из AIConfig (DB-синглтон).
    Возвращает текстовый ответ или пустую строку при ошибке.
    """
    import requests as req
    import json as _json

    cfg = AIConfig.get()

    # Рендерим шаблон тела запроса
    try:
        # Экранируем значения для безопасной вставки внутрь JSON-строки
        prompt_escaped = _json.dumps(prompt)[1:-1]
        session_id_escaped = _json.dumps(session_id)[1:-1]
        log_id_escaped = _json.dumps(log_id)[1:-1]
        body_str = (
            cfg.request_template
            .replace('{prompt}', prompt_escaped)
            .replace('{session_id}', session_id_escaped)
            .replace('{log_id}', log_id_escaped)
        )
        payload = _json.loads(body_str)
    except Exception as e:
        logger.error("LLM request_template render/parse error: %s", e)
        return ''

    headers = {'Content-Type': 'application/json'}
    if cfg.auth_header:
        headers['Authorization'] = cfg.auth_header
    if cfg.referer:
        headers['Referer'] = cfg.referer

    try:
        resp = req.post(cfg.url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("LLM API request failed: %s", e)
        return ''

    # Извлекаем ответ по cfg.response_key
    key = cfg.response_key or 'messages'
    val = data.get(key)
    if isinstance(val, list):
        ai_res = val[-1] if val else ''
    elif isinstance(val, str):
        ai_res = val
    else:
        ai_res = data.get('response', '') or ''

    return (ai_res or '').strip()


def generate_ai_summary(rec: Recording):
    """Генерация названия и краткого саммари через внешний AI API. При любой ошибке — логируем и выходим."""
    text = (rec.transcription or '').strip()
    if not text:
        return
    try:
        import json
        first = text[:1000]
        last = text[-1000:] if len(text) > 1000 else ""
        mid = ""
        if len(text) > 3000:
            mid_point = len(text) // 2
            mid = text[mid_point - 500 : mid_point + 500]
        elif len(text) > 1000:
            mid_pos = len(text) // 2
            mid = text[mid_pos - 250 : mid_pos + 250]
        sample = f"START: {first}\n\nMIDDLE: {mid}\n\nEND: {last}"
        prompt = (
            "Ниже приведены отрывки из транскрибации встречи (начало, середина, конец). "
            "Дай встрече подходящее название и краткое саммари (о чем была встреча). "
            "Ответ должен быть СТРОГО в формате JSON: {\"title\": \"...\", \"summary\": \"...\"}. "
            f"Текст:\n\n{sample}"
        )
        ai_res = call_llm_api(
            prompt=prompt,
            session_id=f"meet_{rec.pk}_thread",
            log_id=f"meet_{rec.pk}",
            timeout=90,
        )
        if not ai_res:
            return
        try:
            clean_res = ai_res
            if clean_res.startswith('```json'):
                clean_res = clean_res.split('```json')[1].split('```')[0].strip()
            elif clean_res.startswith('```'):
                clean_res = clean_res.split('```')[1].split('```')[0].strip()
            res_json = json.loads(clean_res)
            rec.ai_title = (res_json.get('title') or '')[:255]
            rec.ai_summary = res_json.get('summary') or ''
            rec.save(update_fields=['ai_title', 'ai_summary'])
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to parse AI JSON response for rec %s: %s", rec.pk, e)
    except Exception:
        logger.exception("AI Title/Summary generation failed for rec %s", rec.pk)

def process_one_transcribe():
    """Взять одну задачу из очереди транскрибации и выполнить. Возвращает True, если задача была."""
    with transaction.atomic():
        task = get_next_transcribe_task()
        if not task:
            return False
        rec = task.recording
        task.delete()
    if rec.status in (Recording.Status.DONE, Recording.Status.FAILED, Recording.Status.TRANSCRIBING):
        return True
    transcribe_one(rec)
    return True


def process_one_embedding():
    """Взять одну задачу из очереди эмбеддингов и выполнить. Возвращает True, если задача была."""
    with transaction.atomic():
        task = get_next_embedding_task()
        if not task:
            return False
        rec = task.recording
        task.delete()
    index_embedding_for_recording(rec)
    return True
