import logging
import re
import time as _time
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.db import transaction

from .models import Recording, PollLog, Space, AIConfig
from .s3_client import list_mp3_files, download_mp3_to_local
from .queue_services import (
    enqueue_transcribe, enqueue_embedding,
    get_next_transcribe_task, get_next_embedding_task,
    index_embedding_for_recording,
)

logger = logging.getLogger(__name__)

# Кэш результата проверки GPU: None = не проверяли, True/False = результат
_cuda_compute_ok: bool | None = None


def _is_cuda_compute_available() -> bool:
    """Проверяет, работает ли CUDA compute реально (не только torch.cuda.is_available()).

    Кэширует результат — проверка выполняется один раз за жизнь процесса.
    Нужно из-за несовместимости PyTorch cu128 + драйвер 535 (требует ≥550):
    is_available()=True, но любая операция падает с 'operation not supported'.
    """
    global _cuda_compute_ok
    if _cuda_compute_ok is not None:
        return _cuda_compute_ok
    try:
        import torch
        if not torch.cuda.is_available():
            _cuda_compute_ok = False
            return False
        t = torch.zeros(1, device='cuda')
        del t
        torch.cuda.empty_cache()
        _cuda_compute_ok = True
        logger.info('CUDA compute: OK — будет использован GPU')
    except Exception as e:
        _cuda_compute_ok = False
        logger.warning('CUDA compute недоступен (%s) — используем CPU', e)
    return _cuda_compute_ok


# ── pyannote meta-tensor patch ────────────────────────────────────────────────

_pyannote_meta_patched = False


def _patch_pyannote_meta_tensors():
    """Фикс несовместимости pyannote 4.x + torch 2.8+.

    При загрузке checkpoint lightning создаёт meta-тензоры, а module.to(device)
    на них падает с NotImplementedError. Патчим Model.setup чтобы сначала
    материализовать meta-параметры через to_empty(). Применяется однократно.
    """
    global _pyannote_meta_patched
    if _pyannote_meta_patched:
        return
    try:
        import torch
        from pyannote.audio.core import model as _pm
        if getattr(_pm.Model, '_meta_patch_applied', False):
            _pyannote_meta_patched = True
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
        _pyannote_meta_patched = True
    except Exception as e:
        logger.warning('pyannote meta-tensor patch failed: %s', e)


# ── DiarizationWrapper ────────────────────────────────────────────────────────

class _DiarizationWrapper:
    """Обёртка над pyannote Pipeline, совместимая с whisperx.assign_word_speakers.

    whisperx>=3.8 ожидает output.speaker_diarization (community-1 only),
    но pyannote/speaker-diarization-3.1 возвращает Annotation напрямую.
    Нормализует оба варианта и экспонирует .model для извлечения эмбеддингов.
    """

    def __init__(self, model_name: str, token: str, device: str):
        import torch
        from pyannote.audio import Pipeline
        self.model = Pipeline.from_pretrained(model_name, token=token).to(torch.device(device))

    def __call__(self, audio, min_speakers=None, max_speakers=None, num_speakers=None):
        import pandas as pd
        import torch
        from whisperx.audio import SAMPLE_RATE

        audio_data = {'waveform': torch.from_numpy(audio[None, :]), 'sample_rate': SAMPLE_RATE}
        kwargs = {k: v for k, v in {
            'num_speakers': num_speakers,
            'min_speakers': min_speakers,
            'max_speakers': max_speakers,
        }.items() if v is not None}
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


# ── Device selection ──────────────────────────────────────────────────────────

def _select_device(rec=None) -> str:
    """Определить вычислительное устройство для транскрибации.

    Приоритет: per-recording override → OCR GPU mode → авто (CUDA/CPU).
    Считывает и очищает SystemConfig-ключ device_override_{rec.pk}.
    """
    from .models import SystemConfig
    if rec is not None:
        override = SystemConfig.get(f'device_override_{rec.pk}', '')
        if override:
            SystemConfig.set(f'device_override_{rec.pk}', '')
            if override == 'cpu':
                logger.info('WhisperX: принудительно CPU (override)')
                return 'cpu'
            if override == 'gpu':
                dev = 'cuda' if _is_cuda_compute_available() else 'cpu'
                logger.info('WhisperX: принудительно GPU (override) → %s', dev)
                return dev

    ocr_gpu_mode = SystemConfig.get('ocr_gpu_mode', '0') == '1'
    if ocr_gpu_mode:
        logger.info('WhisperX: GPU занят OCR-режимом, используем CPU')
        return 'cpu'

    return 'cuda' if _is_cuda_compute_available() else 'cpu'


# ── WhisperX model loader ─────────────────────────────────────────────────────

def _load_whisper_model(model_size: str, device: str, compute_type: str):
    """Загрузить WhisperX-модель с уменьшенным batch_size у VAD (для 2GB GPU)."""
    import whisperx
    model = whisperx.load_model(model_size, device=device, compute_type=compute_type, language='ru')
    # Уменьшаем batch_size у внутренней VAD-модели pyannote (default=32, OOM на 2GB)
    try:
        model.vad_model.vad_pipeline._inferences['_segmentation'].batch_size = 2
    except Exception:
        try:
            model.vad_model._segmentation.batch_size = 2
        except Exception:
            pass
    return model


# ── Utility helpers ───────────────────────────────────────────────────────────

def get_lib_versions() -> dict:
    """Возвращает версии ключевых библиотек транскрибации (ключи без дефисов — для Django-шаблонов)."""
    versions = {}
    try:
        import importlib.metadata as _meta
        pkg_map = {
            'whisperx': 'whisperx',
            'faster_whisper': 'faster-whisper',
            'pyannote_audio': 'pyannote.audio',
            'torch': 'torch',
            'ctranslate2': 'ctranslate2',
        }
        for key, pkg in pkg_map.items():
            try:
                versions[key] = _meta.version(pkg)
            except Exception:
                versions[key] = '?'
    except Exception:
        pass
    return versions


def _extract_space_slug(s3_key: str):
    """Ищет паттерн -org-{slug} в конце ключа S3."""
    m = re.search(r'-org-([a-z0-9\-]+?)(?:\.mp3)?$', s3_key, re.IGNORECASE)
    return m.group(1) if m else None


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


# ── Diarization helpers ───────────────────────────────────────────────────────

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


# ── Speaker profile matching ──────────────────────────────────────────────────

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
    """Извлечь речевые паттерны спикера из транскрипции — топ слов и биграмм.

    Игнорирует стоп-слова.
    Возвращает {'top_words': {word: count}, 'phrases': {bigram: count}, 'sample_words': N}
    """
    from collections import Counter

    STOP_WORDS = {
        'и', 'в', 'не', 'на', 'я', 'что', 'с', 'он', 'как', 'это', 'но', 'они',
        'мы', 'к', 'из', 'по', 'же', 'а', 'то', 'от', 'за', 'о', 'для', 'до',
        'у', 'вот', 'так', 'да', 'ну', 'вы', 'было', 'есть', 'нет', 'ещё', 'уже',
        'всё', 'если', 'при', 'тоже', 'или', 'бы', 'со', 'всего', 'все', 'он',
        'его', 'её', 'им', 'их', 'мне', 'тут', 'там', 'был', 'она', 'когда', 'тебя',
        'чтобы', 'будет', 'себя', 'этот', 'этого', 'эту', 'эти', 'этой',
    }

    speaker_pattern = re.compile(r'^[\-—]\s*' + re.escape(speaker_id) + r':\s*(.*)')
    words = []
    for line in transcription_text.split('\n'):
        m = speaker_pattern.match(line)
        if m:
            tokens = re.findall(r'[а-яёa-z]{3,}', m.group(1).lower())
            words.extend(t for t in tokens if t not in STOP_WORDS)

    if len(words) < 10:
        return {}

    top_words = dict(Counter(words).most_common(30))

    bigrams = [f'{words[i]} {words[i+1]}' for i in range(len(words) - 1)]
    top_phrases = {k: v for k, v in Counter(bigrams).most_common(20) if v >= 2}

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
        total = sum(merged.values()) or 1
        return {k: round(v * 100 / total, 2) for k, v in sorted(merged.items(), key=lambda x: -x[1])[:30]}

    return {
        'top_words': merge_freq(existing.get('top_words', {}), new.get('top_words', {}), weight_existing),
        'phrases': merge_freq(existing.get('phrases', {}), new.get('phrases', {}), weight_existing),
        'sample_words': existing.get('sample_words', 0) + new.get('sample_words', 0),
    }


def _speech_pattern_similarity(profile_patterns: dict, candidate_patterns: dict) -> float:
    """Сходство речевых паттернов: Jaccard по топ-словам + пересечение биграмм. Возвращает 0.0–1.0."""
    if not profile_patterns or not candidate_patterns:
        return 0.0

    p_words = set(profile_patterns.get('top_words', {}).keys())
    c_words = set(candidate_patterns.get('top_words', {}).keys())
    if not p_words or not c_words:
        return 0.0

    union = len(p_words | c_words)
    word_sim = len(p_words & c_words) / union if union else 0.0

    p_phrases = set(profile_patterns.get('phrases', {}).keys())
    c_phrases = set(candidate_patterns.get('phrases', {}).keys())
    if p_phrases and c_phrases:
        phrase_union = len(p_phrases | c_phrases)
        phrase_sim = len(p_phrases & c_phrases) / phrase_union if phrase_union else 0.0
    else:
        phrase_sim = 0.0

    return word_sim * 0.6 + phrase_sim * 0.4


def match_by_speech_patterns(space, transcription_text: str, speaker_ids: list, threshold=0.30) -> dict:
    """Сопоставить спикеров по речевым паттернам с профилями в БД.

    Используется как дополнение к аудиоэмбеддингам.
    Возвращает {speaker_id: name}.
    """
    from .models import SpeakerProfile
    profiles = list(SpeakerProfile.objects.filter(space=space).exclude(speech_patterns={}))
    if not profiles:
        return {}

    candidate_patterns = {
        sid: p for sid in speaker_ids
        if (p := _extract_speech_patterns(transcription_text, sid))
    }

    scores = [
        (sim, sid, profile.name)
        for sid, cand in candidate_patterns.items()
        for profile in profiles
        if (sim := _speech_pattern_similarity(profile.speech_patterns, cand)) >= threshold
    ]
    scores.sort(reverse=True)

    matched = {}
    already_used: set = set()
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
                    profile.embedding = ((old * n + new_e) / (n + 1)).tolist()
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


# ── Transcription engines ─────────────────────────────────────────────────────

def _run_diarization_foxnose(audio_path: Path):
    """Диаризация через FoxNoseTech/diarize (без токена, ~4 мин на CPU).

    Возвращает pandas DataFrame с колонками start, end, speaker.
    """
    import pandas as pd
    from diarize import diarize as _fox_diarize
    result = _fox_diarize(str(audio_path))
    df = pd.DataFrame([
        {'start': seg.start, 'end': seg.end, 'speaker': seg.speaker}
        for seg in result.segments
    ])
    return df


def _run_diarization_pyannote(audio, device: str = 'cpu'):
    """Диаризация через pyannote/speaker-diarization-3.1 (требует HF-токен).

    Возвращает (diarize_segments DataFrame, diarize_model) или (None, None).
    """
    import torch
    if not settings.HUGGINGFACE_TOKEN:
        return None, None
    try:
        diarize_model = _DiarizationWrapper(
            model_name='pyannote/speaker-diarization-3.1',
            token=settings.HUGGINGFACE_TOKEN,
            device=device,
        )
        diarize_segments = diarize_model(audio, min_speakers=1, max_speakers=10)
        return diarize_segments, diarize_model
    except (torch.cuda.OutOfMemoryError, MemoryError, RuntimeError) as e:
        logger.warning('pyannote ошибка на %s (%s), падаем на CPU', device, e)
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        diarize_model = _DiarizationWrapper(
            model_name='pyannote/speaker-diarization-3.1',
            token=settings.HUGGINGFACE_TOKEN,
            device='cpu',
        )
        diarize_segments = diarize_model(audio, min_speakers=1, max_speakers=10)
        return diarize_segments, diarize_model


def _run_diarization(audio, audio_path: Path = None, rec=None, device: str = 'cpu'):
    """Запуск диаризации: FoxNoseTech/diarize → pyannote fallback.

    Возвращает (diarize_segments, diarize_model).
    diarize_model=None когда используется FoxNoseTech/diarize (нет объекта модели).
    """
    if rec:
        _set_progress(rec, 70, 'Определение спикеров')

    # 1. Пробуем FoxNoseTech/diarize (без токена, быстрее на CPU)
    if audio_path is not None:
        try:
            diarize_segments = _run_diarization_foxnose(audio_path)
            if diarize_segments is not None and not diarize_segments.empty:
                logger.info('Диаризация: FoxNoseTech/diarize (%d сегментов)', len(diarize_segments))
                return diarize_segments, None
        except Exception as e:
            logger.warning('FoxNoseTech/diarize failed (%s), переключаемся на pyannote', e)

    # 2. Fallback: pyannote/speaker-diarization-3.1
    diarize_segments, diarize_model = _run_diarization_pyannote(audio, device=device)
    if diarize_segments is not None:
        logger.info('Диаризация: pyannote (%d строк)', len(diarize_segments))
    return diarize_segments, diarize_model


def _format_dialogue(result: dict, diarize_segments, diarize_model, audio, rec=None) -> str:
    """Назначить спикеров сегментам и собрать диалог.

    result — dict с ключом 'segments' в формате whisperx.
    Если diarize_segments=None — возвращает plain text без спикеров.
    """
    import whisperx

    if diarize_segments is not None:
        if rec:
            _set_progress(rec, 90, 'Сборка диалога')
        result = whisperx.assign_word_speakers(diarize_segments, result)
        speaker_embeddings = _extract_speaker_embeddings(audio, diarize_segments, diarize_model)
    else:
        speaker_embeddings = {}

    speaker_map: dict = {}
    counter = [0]

    def get_name(raw):
        if raw not in speaker_map:
            counter[0] += 1
            speaker_map[raw] = f'Участник {counter[0]}'
        return speaker_map[raw]

    lines = []
    current_speaker = None
    current_text: list = []

    for seg in result['segments']:
        raw = seg.get('speaker') or ('SPEAKER_00' if diarize_segments is not None else None)
        name = get_name(raw) if raw else None
        text = seg.get('text', '').strip()
        if not text:
            continue
        if name is None:
            # plain text без диаризации
            lines.append(text)
        elif name != current_speaker:
            if current_text:
                lines.append(f'— {current_speaker}: {" ".join(current_text)}')
            current_speaker = name
            current_text = [text]
        else:
            current_text.append(text)

    if current_text and current_speaker:
        lines.append(f'— {current_speaker}: {" ".join(current_text)}')

    if speaker_embeddings and speaker_map:
        speaker_embeddings = {speaker_map.get(k, k): v for k, v in speaker_embeddings.items()}
    if rec and speaker_embeddings:
        rec.speaker_embeddings = speaker_embeddings
        rec.save(update_fields=['speaker_embeddings'])

    return '\n'.join(lines) if lines else ''


def _transcribe_whisperx(local_path: Path, rec=None, model_size: str = 'small') -> str:
    """Транскрибация через WhisperX + диаризация pyannote.

    Использует GPU если доступен, иначе CPU.
    """
    import torch
    import whisperx

    _patch_pyannote_meta_tensors()

    device = _select_device(rec)
    compute_type = 'int8'
    logger.info('WhisperX device: %s, compute_type: %s', device, compute_type)

    # 1. Загружаем аудио
    if rec:
        _set_progress(rec, 10, 'Загрузка аудио')
    audio = whisperx.load_audio(str(local_path))

    # 2. Транскрипция
    if rec:
        _set_progress(rec, 25, 'Транскрипция речи')
    try:
        model = _load_whisper_model(model_size, device, compute_type)
        result = model.transcribe(audio, language='ru', batch_size=4)
    except (torch.cuda.OutOfMemoryError, MemoryError, RuntimeError) as e:
        logger.warning('GPU ошибка при транскрипции (%s), падаем на CPU', e)
        if device == 'cuda':
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
            device = 'cpu'
        model = _load_whisper_model(model_size, device, compute_type)
        result = model.transcribe(audio, language='ru', batch_size=4)
    del model

    # 3. Выравнивание слов по времени (нужно для диаризации)
    if rec:
        _set_progress(rec, 55, 'Выравнивание по времени')
    try:
        align_model, metadata = whisperx.load_align_model(language_code='ru', device=device)
        result = whisperx.align(result['segments'], align_model, metadata, audio, device)
        del align_model
    except (torch.cuda.OutOfMemoryError, MemoryError, RuntimeError):
        logger.warning('GPU ошибка при align, падаем на CPU')
        if device == 'cuda':
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
            device = 'cpu'
        try:
            align_model, metadata = whisperx.load_align_model(language_code='ru', device=device)
            result = whisperx.align(result['segments'], align_model, metadata, audio, device)
            del align_model
        except Exception as e:
            logger.warning('Align недоступен, пропускаем: %s', e)
    except Exception as e:
        logger.warning('Align model недоступен, пропускаем выравнивание: %s', e)

    # 4. Диаризация и сборка диалога
    diarize_segments, diarize_model = _run_diarization(audio, audio_path=local_path, rec=rec, device=device)
    text = _format_dialogue(result, diarize_segments, diarize_model, audio, rec=rec)
    if diarize_model:
        del diarize_model
    return text


def _transcribe_faster_whisper_diarized(
    local_path: Path, model_size: str = 'small',
    device: str = 'cpu', compute_type: str = 'int8', rec=None,
) -> str:
    """faster-whisper (транскрипция) + pyannote CPU (диаризация спикеров).

    Используется как fallback когда whisperX недоступен.
    Диаризация всегда на CPU — не требует GPU для pyannote.
    """
    import whisperx
    from faster_whisper import WhisperModel

    logger.info('faster-whisper diarized: device=%s model=%s compute=%s', device, model_size, compute_type)

    # 1. Загружаем аудио через whisperx (нужен и для диаризации)
    if rec:
        _set_progress(rec, 10, 'Загрузка аудио')
    audio = whisperx.load_audio(str(local_path))

    # 2. Транскрипция с временны́ми метками слов
    if rec:
        _set_progress(rec, 30, f'Транскрипция речи ({model_size})')
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments_gen, _ = model.transcribe(
        str(local_path), language='ru', beam_size=5, word_timestamps=True,
    )
    segments = list(segments_gen)
    del model

    # 3. Конвертируем в формат whisperx для assign_word_speakers
    result = {
        'segments': [
            {
                'text': s.text,
                'start': s.start,
                'end': s.end,
                'words': [
                    {'word': w.word, 'start': w.start, 'end': w.end, 'score': w.probability}
                    for w in (s.words or [])
                ],
            }
            for s in segments
        ]
    }

    # 4. Диаризация FoxNoseTech→pyannote fallback + сборка диалога
    diarize_segments, diarize_model = _run_diarization(audio, audio_path=local_path, rec=rec, device='cpu')
    text = _format_dialogue(result, diarize_segments, diarize_model, audio, rec=rec)
    if diarize_model:
        del diarize_model
    return text


def _transcribe_faster_whisper(
    local_path: Path, model_size: str = 'small',
    device_hint: str = 'auto', compute_type: str = 'int8',
) -> str:
    """Локальная транскрибация через faster-whisper (без диаризации).

    device_hint='gpu'  → только GPU (выбрасывает исключение при недоступности)
    device_hint='cpu'  → только CPU
    device_hint='auto' → GPU если доступен, иначе CPU
    """
    from faster_whisper import WhisperModel

    if device_hint == 'cpu':
        device = 'cpu'
        effective_compute = 'int8'  # int8_float16 требует CUDA
    elif device_hint == 'gpu':
        if not _is_cuda_compute_available():
            raise RuntimeError('CUDA недоступен')
        device = 'cuda'
        effective_compute = compute_type
    else:
        device = 'cuda' if _is_cuda_compute_available() else 'cpu'
        effective_compute = compute_type if device == 'cuda' else 'int8'

    logger.info('faster-whisper: device=%s model=%s compute=%s', device, model_size, effective_compute)
    model = WhisperModel(model_size, device=device, compute_type=effective_compute)
    segments, _ = model.transcribe(str(local_path), language='ru', beam_size=5)
    return ' '.join(s.text for s in segments).strip()


# ── Main transcription entry point ────────────────────────────────────────────

def transcribe_one(rec: Recording, trigger: str = ''):
    """Скачать MP3, выполнить транскрипцию с диаризацией (whisperx) или без (faster-whisper).

    trigger: 'auto' — поллер, 'manual' — пользователь нажал кнопку, 'restart' — после перезапуска.
    """
    # Читаем и очищаем trigger (manual / restart / auto)
    from .models import SystemConfig as _SC_trigger
    stored_trigger = _SC_trigger.get(f'trigger_{rec.pk}', '')
    if stored_trigger:
        _SC_trigger.set(f'trigger_{rec.pk}', '')
        trigger = stored_trigger
    if not trigger:
        trigger = 'auto'

    attempt_started = timezone.now()
    attempt_steps: list = []

    def _step(name: str, **meta):
        """Контекстный менеджер-like хелпер для записи шага с таймингом."""
        class _Step:
            def __init__(self):
                self.t0 = _time.monotonic()
                self.info = {'name': name, **meta}
            def done(self, **extra):
                self.info['duration_sec'] = round(_time.monotonic() - self.t0, 2)
                self.info['ok'] = True
                self.info.update(extra)
                attempt_steps.append(self.info)
                logger.info('Step %s done in %.1fs', name, self.info['duration_sec'])
            def fail(self, err):
                self.info['duration_sec'] = round(_time.monotonic() - self.t0, 2)
                self.info['ok'] = False
                self.info['error'] = str(err)
                attempt_steps.append(self.info)
                logger.warning('Step %s failed in %.1fs: %s', name, self.info['duration_sec'], err)
        return _Step()

    Recording.objects.filter(pk=rec.pk).update(
        status=Recording.Status.TRANSCRIBING,
        error_message='',
        transcription_progress=5,
        transcription_stage='Скачивание файла',
        updated_at=timezone.now(),
    )
    rec.status = Recording.Status.TRANSCRIBING
    rec.error_message = ''
    rec.transcription_progress = 5
    rec.transcription_stage = 'Скачивание файла'

    download_dir = Path(settings.RECORDINGS_DOWNLOAD_DIR)
    download_dir.mkdir(parents=True, exist_ok=True)
    safe_name = rec.s3_key.replace('/', '_')
    local_path = download_dir / safe_name

    attempt_result = 'failed'
    attempt_error = None

    try:
        # ── Шаг 1: скачивание ────────────────────────────────────────────────
        s = _step('download', file=rec.s3_key)
        download_mp3_to_local(rec.s3_key, local_path)
        s.done(size_bytes=local_path.stat().st_size if local_path.exists() else 0)

        # Читаем и сразу очищаем per-recording overrides
        from .models import SystemConfig as _SC
        engine_override = _SC.get(f'engine_override_{rec.pk}', '')
        model_override = _SC.get(f'model_override_{rec.pk}', '')
        if engine_override:
            _SC.set(f'engine_override_{rec.pk}', '')
        if model_override:
            _SC.set(f'model_override_{rec.pk}', '')

        model_size = model_override or rec.transcription_quality or 'small'
        wx_model_size = 'tiny' if model_size == 'base' else model_size
        use_whisperx = bool(settings.HUGGINGFACE_TOKEN) and engine_override != 'faster'
        device_hint = _SC.get(f'device_override_{rec.pk}', '') or 'auto'
        if device_hint != 'auto':
            _SC.set(f'device_override_{rec.pk}', '')

        text = None

        # ── Fallback-цепочка ──────────────────────────────────────────────────────
        # 1. whisperX GPU           — транскрипция + диаризация, лучшее качество
        # 2. large-v3-turbo GPU     — faster-whisper GPU + FoxNoseTech/pyannote диаризация
        # 3. model_size CPU         — faster-whisper CPU + FoxNoseTech/pyannote диаризация
        # ─────────────────────────────────────────────────────────────────────────
        if use_whisperx and engine_override != 'faster':
            s = _step('transcription', engine='whisperx', model=wx_model_size, device='gpu→cpu')
            try:
                text = _transcribe_whisperx(local_path, rec=rec, model_size=wx_model_size)
                s.done()
            except Exception as e:
                s.fail(e)
                logger.warning('WhisperX failed (%s), пробуем large-v3-turbo GPU', e)

        if text is None:
            s = _step('transcription', engine='faster-whisper', model='large-v3-turbo', device='cuda', compute='int8_float16', diarization='foxnose→pyannote CPU')
            try:
                text = _transcribe_faster_whisper_diarized(
                    local_path, model_size='large-v3-turbo',
                    device='cuda', compute_type='int8_float16', rec=rec,
                )
                s.done()
            except Exception as e:
                s.fail(e)
                logger.warning('large-v3-turbo GPU failed (%s), пробуем %s CPU', e, model_size)

        if text is None:
            # На CPU large/medium вызывают kernel OOM и убивают контейнер (~3GB+ RSS).
            # Разрешённые модели для CPU: только tiny, base, small.
            _CPU_ALLOWED = ('tiny', 'base', 'small')
            cpu_model_size = model_size if model_size in _CPU_ALLOWED else 'small'
            if cpu_model_size != model_size:
                logger.warning('CPU fallback: модель %s слишком большая для RAM, используем small', model_size)
            s = _step('transcription', engine='faster-whisper', model=cpu_model_size, device='cpu', compute='int8', diarization='foxnose→pyannote CPU')
            text = _transcribe_faster_whisper_diarized(
                local_path, model_size=cpu_model_size,
                device='cpu', compute_type='int8', rec=rec,
            )
            s.done()

        # Если все варианты вернули пустую строку (битый/тихий файл) — считаем ошибкой
        if not text or not text.strip():
            raise ValueError('Файл не содержит распознаваемой речи (возможно битый или пустой)')

        rec.transcription = text
        rec.transcribed_at = timezone.now()
        rec.status = Recording.Status.DONE
        rec.error_message = ''
        rec.transcription_progress = 100
        rec.transcription_stage = 'Готово'
        attempt_result = 'done'

        # Авто-матчинг имён спикеров (аудиоэмбеддинги + речевые паттерны)
        if rec.space:
            auto_names = {}
            if rec.speaker_embeddings and settings.HUGGINGFACE_TOKEN:
                auto_names = match_speaker_profiles(rec.space, rec.speaker_embeddings)
            all_speaker_ids = list(rec.speaker_embeddings.keys()) if rec.speaker_embeddings else []
            unmatched = [sid for sid in all_speaker_ids if sid not in auto_names]
            if unmatched and text:
                auto_names.update(match_by_speech_patterns(rec.space, text, unmatched))
            if auto_names:
                rec.speaker_names = auto_names

    except Exception as e:
        logger.exception('Transcribe failed for %s', rec.s3_key)
        rec.status = Recording.Status.FAILED
        rec.error_message = str(e)
        rec.transcription_progress = 0
        rec.transcription_stage = ''
        attempt_error = str(e)
    finally:
        # Сохраняем лог попытки
        log_entry = {
            'started_at': attempt_started.isoformat(),
            'ended_at': timezone.now().isoformat(),
            'trigger': trigger,
            'result': attempt_result,
            'steps': attempt_steps,
        }
        if attempt_error:
            log_entry['error'] = attempt_error
        existing_log = list(rec.transcription_log or [])
        existing_log.append(log_entry)
        rec.transcription_log = existing_log

        rec.save()
        if local_path.exists():
            try:
                local_path.unlink()
            except OSError:
                pass
        if rec.status == Recording.Status.DONE:
            s = _step('embedding', model=getattr(settings, 'EMBEDDING_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2'))
            ok = index_embedding_for_recording(rec)
            s.done(ok=ok)
            # Перезаписываем лог после эмбеддинга
            existing_log[-1]['steps'] = attempt_steps
            rec.transcription_log = existing_log
            rec.save(update_fields=['transcription_log'])

            generate_ai_summary(rec)
            notify_meeting_attendees_transcription(rec)
            notify_tg_transcription_done(rec)


# ── S3 polling ────────────────────────────────────────────────────────────────

def reset_hung_transcriptions():
    """Сбросить записи, зависшие в TRANSCRIBING дольше 2 часов → FAILED (без повторной постановки в очередь)."""
    threshold = timezone.now() - timezone.timedelta(hours=2)
    hung = Recording.objects.filter(status=Recording.Status.TRANSCRIBING, updated_at__lt=threshold)
    for rec in hung:
        logger.warning('Hung transcription (2h timeout) → failed: id=%s %s', rec.pk, rec.filename)
        rec.status = Recording.Status.FAILED
        rec.transcription_progress = 0
        rec.transcription_stage = ''
        rec.error_message = 'Прервано: процесс завис дольше 2 часов'
        rec.save(update_fields=['status', 'transcription_progress', 'transcription_stage', 'error_message'])


def run_poll_and_transcribe():
    """Один цикл: опрос S3, обновление стабильности, добавление стабильных в очередь."""
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

            rec.last_size_check_at = now

            if size != rec.size_bytes:
                rec.size_bytes = size
                rec.size_stable_since = None
            else:
                if rec.size_stable_since is None:
                    rec.size_stable_since = now
                if (now - rec.size_stable_since) >= stable_delta:
                    rec.status = Recording.Status.STABLE
                    log.files_stable += 1
                    enqueue_transcribe(rec, priority=0)

            rec.save()

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


# ── Queue processors ──────────────────────────────────────────────────────────

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


# ── AI summary ────────────────────────────────────────────────────────────────

def call_llm_api(prompt: str, session_id: str, log_id: str, timeout: int = 120) -> str:
    """Вызов LLM API с настройками из AIConfig (DB-синглтон).

    Возвращает текстовый ответ или пустую строку при ошибке.
    """
    import requests as req
    import json as _json

    cfg = AIConfig.get()

    try:
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
        logger.error('LLM request_template render/parse error: %s', e)
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
        logger.error('LLM API request failed: %s', e)
        return ''

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
    """Генерация названия и краткого саммари через внешний AI API."""
    text = (rec.transcription or '').strip()
    if not text:
        return
    try:
        import json

        first = text[:1000]
        last = text[-1000:] if len(text) > 1000 else ''
        if len(text) > 3000:
            mid_point = len(text) // 2
            mid = text[mid_point - 500:mid_point + 500]
        elif len(text) > 1000:
            mid_pos = len(text) // 2
            mid = text[mid_pos - 250:mid_pos + 250]
        else:
            mid = ''
        sample = f'START: {first}\n\nMIDDLE: {mid}\n\nEND: {last}'
        prompt = (
            'Ниже приведены отрывки из транскрибации встречи (начало, середина, конец). '
            'Дай встрече подходящее название и краткое саммари (о чем была встреча). '
            'Ответ должен быть СТРОГО в формате JSON: {"title": "...", "summary": "..."}. '
            f'Текст:\n\n{sample}'
        )
        ai_res = call_llm_api(
            prompt=prompt,
            session_id=f'meet_{rec.pk}_thread',
            log_id=f'meet_{rec.pk}',
            timeout=90,
        )
        if not ai_res:
            return

        clean_res = ai_res
        if clean_res.startswith('```json'):
            clean_res = clean_res.split('```json')[1].split('```')[0].strip()
        elif clean_res.startswith('```'):
            clean_res = clean_res.split('```')[1].split('```')[0].strip()

        try:
            res_json = json.loads(clean_res)
            rec.ai_title = (res_json.get('title') or '')[:255]
            rec.ai_summary = res_json.get('summary') or ''
            rec.save(update_fields=['ai_title', 'ai_summary'])
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning('Failed to parse AI JSON response for rec %s: %s', rec.pk, e)
    except Exception:
        logger.exception('AI Title/Summary generation failed for rec %s', rec.pk)


# ── Notifications ─────────────────────────────────────────────────────────────

def notify_meeting_attendees_transcription(rec: Recording):
    """После транскрипции: найти встречу по имени файла и отправить участникам ссылку на запись."""
    try:
        from .models import MeetingRoom, MeetingAttendee
        from . import telegram_service

        fn_pattern = re.compile(
            r'^(\d{4}-\d{2}-\d{2}T[\d.:]+Z)-(.+?)(?:-org-[a-z0-9-]+)?\.mp3$',
            re.IGNORECASE,
        )
        m = fn_pattern.match(rec.filename)
        if not m:
            return
        room_code = m.group(2)
        try:
            meeting = MeetingRoom.objects.get(room_name=room_code)
        except MeetingRoom.DoesNotExist:
            return

        attendees = MeetingAttendee.objects.filter(
            meeting=meeting, user__tg_chat_id__isnull=False
        ).select_related('user')
        if not attendees.exists():
            return

        site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
        rec_url = f'{site_url}/r/{rec.pk}/'
        title = rec.ai_title or rec.filename
        msg = (
            f'📝 Готова транскрипция встречи «{meeting.title}»\n\n'
            f'🎙 Запись: {title}\n'
            f'🔗 {rec_url}'
        )
        for att in attendees:
            try:
                telegram_service.send_message(att.user.tg_chat_id, msg)
            except Exception as e:
                logger.warning('Failed to notify attendee %s: %s', att.user.pk, e)
    except Exception as e:
        logger.exception('notify_meeting_attendees_transcription failed: %s', e)


def notify_tg_transcription_done(rec: Recording):
    """После транскрипции: найти BotChatHistory с audio-сообщением по этой записи
    и отправить транскрипт обратно в Telegram. Если бот в режиме db_agent — кормим агента."""
    try:
        from .models import BotChatHistory, CustomBot, SystemConfig
        from . import telegram_service

        entries = list(BotChatHistory.objects.filter(recording=rec, role='audio').order_by('created_at'))
        if not entries:
            return

        transcription = (rec.transcription or '').strip()
        if not transcription:
            return

        site_url = getattr(__import__('django.conf', fromlist=['settings']).settings, 'SITE_URL', '').rstrip('/')
        rec_url = f'{site_url}/r/{rec.pk}/'

        for entry in entries:
            chat_id = entry.chat_id
            bot_id = entry.bot_id
            token = None
            if bot_id:
                cb = CustomBot.objects.filter(pk=bot_id).first()
                token = cb.token if cb else None

            # Определяем режим
            if bot_id:
                mode = SystemConfig.get(f'db_bot_{bot_id}_answer_mode', 'wiki_rag')
            else:
                mode = SystemConfig.get('main_bot_answer_mode', 'wiki_rag')

            if mode == 'db_agent':
                # Кормим транскрипт в DB-агент
                try:
                    from chemico_agent.agent import ask as db_ask
                    preview = transcription[:500]
                    question = (
                        f'Пользователь прислал голосовое сообщение. Транскрипция:\n"{preview}"\n\n'
                        f'Если это вопрос по базе данных — ответь на него. '
                        f'Если нужны уточнения — спроси. '
                        f'Если это не вопрос к БД — кратко ответь своими словами.'
                    )
                    telegram_service.send_message(chat_id, '🎙 Транскрибировано, анализирую...', token=token)
                    result = db_ask(question, chat_id=chat_id)
                    answer = result.get('text', 'Нет ответа')
                    provider = result.get('provider', '?')
                    model = result.get('model', '?')
                    footer = f'\n\n_({provider} / {model})_'
                    if len(answer) + len(footer) > 4090:
                        answer = answer[:4050] + '...'
                    telegram_service.send_message(chat_id, answer + footer, token=token)
                    import os
                    excel_path = result.get('excel_path')
                    if excel_path and os.path.exists(excel_path):
                        from chemico_agent.telegram import _send_document
                        _send_document(chat_id, excel_path, caption='📊 Данные из Chemico DB', token=token)
                        try:
                            os.remove(excel_path)
                        except OSError:
                            pass
                except Exception:
                    logger.exception('notify_tg_transcription_done db_agent error')
                    # fallback — отправляем просто транскрипт
                    short = transcription[:3000]
                    telegram_service.send_inline(
                        chat_id,
                        f'🎙 *Транскрипция готова*\n\n{short}{"..." if len(transcription) > 3000 else ""}',
                        [[{'text': '📄 Открыть запись', 'url': rec_url}]],
                        token=token,
                    )
            else:
                # Обычный режим — просто отправляем транскрипт
                short = transcription[:3000]
                telegram_service.send_inline(
                    chat_id,
                    f'🎙 *Транскрипция готова*\n\n{short}{"..." if len(transcription) > 3000 else ""}',
                    [[{'text': '📄 Открыть запись', 'url': rec_url}]],
                    token=token,
                )

            # Сохраняем в историю
            BotChatHistory.add(chat_id, bot_id, 'assistant', f'[Транскрипция]: {transcription[:500]}', recording=rec)

    except Exception:
        logger.exception('notify_tg_transcription_done failed')


def check_meeting_notifications():
    """Отправить TG-напоминания о предстоящих встречах."""
    from datetime import timedelta
    from recordings.models import MeetingAttendee
    from recordings import telegram_service

    now = timezone.now()
    attendees = MeetingAttendee.objects.select_related('user', 'meeting').filter(
        confirmed_at__isnull=True,
        meeting__ended_at__isnull=True,
        meeting__scheduled_at__isnull=False,
    )
    for att in attendees:
        scheduled = att.meeting.scheduled_at
        notify_at = scheduled - timedelta(minutes=att.notify_before_minutes)
        if now < notify_at:
            continue
        if scheduled < now - timedelta(minutes=30):
            continue
        if not att.user.tg_chat_id:
            continue
        if att.last_notified_at is not None:
            continue

        room = att.meeting
        join_url = f'https://meet.business-pad.com/rooms/{room.room_name}'
        import zoneinfo as _zi
        _msk = _zi.ZoneInfo('Europe/Moscow')
        sched_msk = scheduled.astimezone(_msk)
        date_str = sched_msk.strftime('%d.%m.%Y')
        start_str = sched_msk.strftime('%H:%M')
        time_line = f'🗓 {date_str}  🕐 {start_str} МСК'
        if room.ended_at:
            time_line += f' — {room.ended_at.astimezone(_msk).strftime("%H:%M")}'
        msg = (
            f'⏰ *Напоминание о встрече через 15 минут!*\n\n'
            f'📅 {room.title}\n'
            f'{time_line}\n\n'
            f'🔗 {join_url}'
        )
        keyboard = [[{'text': '✅ Я зашёл на встречу', 'callback_data': f'confirm_meeting:{att.pk}'}]]
        try:
            telegram_service.send_inline(att.user.tg_chat_id, msg, keyboard)
            att.last_notified_at = now
            att.save(update_fields=['last_notified_at'])
        except Exception as e:
            logger.warning('Failed to notify user %s: %s', att.user.email, e)
