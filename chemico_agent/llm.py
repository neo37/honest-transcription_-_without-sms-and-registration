"""
LangChain LLM-фабрика для chemico-агента.

Поддерживаемые провайдеры (переключаются через SystemConfig.llm_provider):
  openai    → ChatOpenAI (api.openai.com)
  anthropic → ChatAnthropic (claude-*)
  grok      → ChatOpenAI(base_url=x.ai)  — OpenAI-compatible
  gonka     → ChatOpenAI(base_url=gonka) — OpenAI-compatible

По умолчанию: openai → gpt-4o-mini.
Для chemico-агента можно переопределить провайдер через:
  SystemConfig.set('chemico_llm_provider', 'anthropic')  — только для агента
  SystemConfig.set('chemico_llm_model', 'claude-sonnet-4-6')
  Если не задан — берётся глобальный llm_provider.
"""
from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

_GONKA_NODES = [
    'https://node3.gonka.ai',
    'http://node1.gonka.ai:8000',
    'http://node2.gonka.ai:8000',
]

_DEFAULT_MODELS = {
    'openai':    'gpt-4o-mini',
    'anthropic': 'claude-sonnet-4-6',
    'grok':      'grok-3-mini-fast',
    'gonka':     'Qwen/Qwen3-235B-A22B-Instruct-2507-FP8',
}


def _get_config() -> tuple[str, str, str]:
    """Возвращает (provider, credential, model) для chemico-агента.

    Приоритет:
      1. chemico_llm_provider / chemico_llm_model в SystemConfig
      2. Глобальный llm_provider / llm_model из bot_agent._get_llm_config()
    """
    from recordings.models import SystemConfig

    # Chemico-специфичный провайдер
    chemico_provider = SystemConfig.get('chemico_llm_provider', '').strip().lower()
    chemico_model    = SystemConfig.get('chemico_llm_model', '').strip()

    if chemico_provider:
        provider = chemico_provider
        model = chemico_model or _DEFAULT_MODELS.get(provider, _DEFAULT_MODELS['openai'])
        credential = _get_credential(provider)
        return provider, credential, model

    # Фолбек на глобальный конфиг из bot_agent
    try:
        from recordings.bot_agent import _get_llm_config
        return _get_llm_config()
    except Exception:
        pass

    # Последний фолбек
    openai_key = SystemConfig.get('openai_api_key', '') or os.environ.get('OPENAI_API_KEY', '')
    return 'openai', openai_key, _DEFAULT_MODELS['openai']


def _get_credential(provider: str) -> str:
    from recordings.models import SystemConfig
    if provider == 'openai':
        return SystemConfig.get('openai_api_key', '') or os.environ.get('OPENAI_API_KEY', '')
    if provider == 'anthropic':
        return SystemConfig.get('anthropic_api_key', '') or os.environ.get('ANTHROPIC_API_KEY', '')
    if provider == 'grok':
        return SystemConfig.get('grok_api_key', '') or os.environ.get('GROK_API_KEY', '')
    if provider == 'gonka':
        return os.environ.get('GONKA_PRIVATE_KEY', '') or SystemConfig.get('gonka_private_key', '')
    return ''


def get_langchain_llm(temperature: float = 0.0):
    """Возвращает настроенный LangChain Chat-модель для текущего провайдера."""
    provider, credential, model = _get_config()
    logger.info('chemico_agent LLM: provider=%s model=%s', provider, model)

    if provider == 'anthropic':
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            anthropic_api_key=credential,
            model=model,
            temperature=temperature,
            timeout=120,
            max_retries=1,
        )

    # OpenAI-совместимые: openai / grok / gonka
    from langchain_openai import ChatOpenAI

    if provider == 'openai':
        return ChatOpenAI(
            api_key=credential,
            model=model,
            temperature=temperature,
            timeout=180,
            max_retries=1,
        )

    if provider == 'grok':
        return ChatOpenAI(
            api_key=credential,
            model=model,
            base_url='https://api.x.ai/v1',
            temperature=temperature,
            timeout=120,
            max_retries=1,
        )

    if provider == 'gonka':
        gonka_address = os.environ.get('GONKA_ADDRESS', '')
        base_url = _GONKA_NODES[0] + '/v1'
        return ChatOpenAI(
            api_key=credential or 'gonka',
            model=model,
            base_url=base_url,
            temperature=temperature,
            timeout=120,
            max_retries=1,
        )

    raise ValueError(f'Неизвестный LLM-провайдер: {provider}')


def get_provider_info() -> dict:
    """Для отображения в UI/логах — какой провайдер сейчас активен."""
    provider, _, model = _get_config()
    return {'provider': provider, 'model': model}
