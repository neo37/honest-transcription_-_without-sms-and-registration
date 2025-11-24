"""Улучшенный сервис для работы с LLM API с классификацией по принципу MCP"""
import asyncio
import aiohttp
import logging
from typing import Dict, Any
from config import LLM_API_URL, LLM_API_AUTH, LLM_TEMPERATURE, LLM_MAX_TOKENS

logger = logging.getLogger(__name__)


async def ask_llm(question: str, context: str = "", system_prompt: str = "") -> str:
    """
    Отправка запроса в LLM API (без указания модели - используется дефолтная)
    
    Args:
        question: Вопрос пользователя
        context: Контекст диалога (опционально)
        system_prompt: Системный промпт для настройки поведения LLM
    
    Returns:
        Ответ от LLM
    """
    full_question = f"{context}\n\nВопрос: {question}" if context else question
    
    # Если есть системный промпт, добавляем его
    if system_prompt:
        full_question = f"{system_prompt}\n\n{full_question}"
    
    payload = {
        "question_to_send": full_question,
        "user": "openai",
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": LLM_TEMPERATURE
    }
    
    headers = {
        "Authorization": LLM_API_AUTH,
        "Content-Type": "application/json"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(LLM_API_URL, json=payload, headers=headers, timeout=30) as response:
                if response.status == 200:
                    result = await response.json()
                    if isinstance(result, dict):
                        return result.get("response", result.get("answer", str(result)))
                    return str(result)
                else:
                    error_text = await response.text()
                    logger.error(f"LLM API error: {response.status} - {error_text}")
                    return "Извините, произошла ошибка при обработке вашего вопроса. Попробуйте переформулировать."
    except asyncio.TimeoutError:
        logger.error("LLM API timeout")
        return "Извините, временно не могу обработать ваш вопрос. Пожалуйста, вернитесь к основному диалогу."
    except Exception as e:
        logger.error(f"LLM API exception: {e}")
        return "Извините, временно не могу обработать ваш вопрос. Пожалуйста, вернитесь к основному диалогу."


async def classify_user_message(user_message: str, current_state: str = "", dialog_context: str = "") -> Dict[str, Any]:
    """
    Классификация сообщения пользователя по принципу MCP
    
    Args:
        user_message: Сообщение пользователя
        current_state: Текущее состояние диалога
        dialog_context: Контекст диалога
    
    Returns:
        Словарь с классификацией: {
            "intent": "answer" | "question" | "greeting" | "refusal" | "confirmation" | "other",
            "is_valid_answer": bool,
            "needs_clarification": bool,
            "should_redirect_to_manager": bool,
            "confidence": float (0-1)
        }
    """
    classification_prompt = """Ты - классификатор сообщений для бота регистрации транспорта.

Проанализируй сообщение пользователя и определи:
1. Тип намерения (intent):
   - "answer" - ответ на текущий вопрос бота (да, нет, конкретное значение)
   - "question" - вопрос пользователя
   - "greeting" - приветствие
   - "refusal" - отказ продолжать
   - "confirmation" - подтверждение
   - "other" - другое

2. Является ли это валидным ответом на текущий вопрос бота (is_valid_answer: true/false)
3. Нужно ли уточнение (needs_clarification: true/false)
4. Нужно ли перенаправить к менеджеру (should_redirect_to_manager: true/false)

Верни ответ ТОЛЬКО в формате JSON:
{
    "intent": "answer|question|greeting|refusal|confirmation|other",
    "is_valid_answer": true/false,
    "needs_clarification": true/false,
    "should_redirect_to_manager": true/false,
    "confidence": 0.0-1.0
}

Текущее состояние: {current_state}
Контекст: {dialog_context}
Сообщение пользователя: {user_message}

Верни ТОЛЬКО JSON, без дополнительных комментариев."""

    try:
        context = f"Текущее состояние: {current_state}\nКонтекст: {dialog_context}" if dialog_context else f"Текущее состояние: {current_state}"
        response = await ask_llm(
            question=user_message,
            context=context,
            system_prompt=classification_prompt.format(
                current_state=current_state or "начало диалога",
                dialog_context=dialog_context or "нет контекста",
                user_message=user_message
            )
        )
        
        # Парсим JSON ответ
        import json
        # Убираем markdown код блоки если есть
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        classification = json.loads(response)
        
        # Валидация результата
        if "intent" not in classification:
            classification["intent"] = "other"
        if "is_valid_answer" not in classification:
            classification["is_valid_answer"] = False
        if "needs_clarification" not in classification:
            classification["needs_clarification"] = False
        if "should_redirect_to_manager" not in classification:
            classification["should_redirect_to_manager"] = False
        if "confidence" not in classification:
            classification["confidence"] = 0.5
        
        return classification
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing classification JSON: {e}, response: {response}")
        # Возвращаем дефолтную классификацию
        return {
            "intent": "other",
            "is_valid_answer": False,
            "needs_clarification": True,
            "should_redirect_to_manager": False,
            "confidence": 0.0
        }
    except Exception as e:
        logger.error(f"Error classifying message: {e}")
        return {
            "intent": "other",
            "is_valid_answer": False,
            "needs_clarification": True,
            "should_redirect_to_manager": False,
            "confidence": 0.0
        }


async def generate_humanized_response(
    user_message: str,
    classification: Dict[str, Any],
    current_state: str = "",
    dialog_context: str = ""
) -> str:
    """
    Генерация очеловеченного ответа на основе классификации (MCP принцип)
    
    Args:
        user_message: Сообщение пользователя
        classification: Результат классификации
        current_state: Текущее состояние диалога
        dialog_context: Контекст диалога
    
    Returns:
        Очеловеченный ответ бота
    """
    intent = classification.get("intent", "other")
    is_valid_answer = classification.get("is_valid_answer", False)
    needs_clarification = classification.get("needs_clarification", False)
    should_redirect = classification.get("should_redirect_to_manager", False)
    
    # Формируем контекст для генерации ответа
    system_context = """Ты - дружелюбный помощник в Telegram-боте для сбора информации о свободном транспорте от водителей-партнеров.

Твоя задача:
- Вежливо и по-человечески отвечать на вопросы водителей
- Показывать понимание и эмпатию
- Мягко направлять к заполнению анкеты, если водитель отвлекся
- Используй естественный разговорный стиль, как живой человек
- Будь дружелюбным, но профессиональным

Важно: Отвечай так, как бы ответил живой человек, а не робот. Используй разговорный стиль, но оставайся вежливым."""

    # Формируем инструкции в зависимости от классификации
    instructions = ""
    
    if should_redirect:
        instructions = "Пользователь задал сложный вопрос, который требует подключения менеджера. Вежливо предложи связаться с менеджером."
    elif intent == "question":
        instructions = "Пользователь задал вопрос. Ответь на него дружелюбно и понятно, но затем мягко напомни о необходимости продолжить заполнение анкеты."
    elif intent == "refusal":
        instructions = "Пользователь отказался продолжать. Вежливо прими это и предложи вернуться позже."
    elif not is_valid_answer and needs_clarification:
        instructions = "Ответ пользователя не совсем понятен или не соответствует текущему вопросу. Вежливо попроси уточнить или повторить ответ."
    elif is_valid_answer:
        instructions = "Пользователь дал валидный ответ. Подтверди это дружелюбно и переходи к следующему шагу."
    else:
        instructions = "Пользователь написал что-то неожиданное. Вежливо ответь и мягко направь к заполнению анкеты."

    full_context = f"""{system_context}

Текущее состояние диалога: {current_state}
Контекст: {dialog_context}
Классификация сообщения:
- Тип намерения: {intent}
- Валидный ответ: {is_valid_answer}
- Нужно уточнение: {needs_clarification}
- Перенаправить к менеджеру: {should_redirect}

Инструкция: {instructions}

Сообщение пользователя: {user_message}

Сгенерируй дружелюбный, человечный ответ на основе классификации и инструкций."""

    try:
        response = await ask_llm(
            question=user_message,
            context=full_context,
            system_prompt=system_context
        )
        return response.strip()
    except Exception as e:
        logger.error(f"Error generating response: {e}")
        return "Извините, произошла ошибка. Пожалуйста, попробуйте еще раз."


async def handle_user_question(user_message: str, dialog_context: str = "", current_state: str = "") -> str:
    """
    Обработка вопроса пользователя с классификацией по принципу MCP
    
    Args:
        user_message: Сообщение пользователя
        dialog_context: Контекст текущего диалога
        current_state: Текущее состояние диалога
    
    Returns:
        Очеловеченный ответ бота
    """
    # Шаг 1: Классификация сообщения (MCP принцип)
    classification = await classify_user_message(user_message, current_state, dialog_context)
    
    logger.info(f"Message classified: {classification}")
    
    # Шаг 2: Генерация ответа на основе классификации
    response = await generate_humanized_response(
        user_message,
        classification,
        current_state,
        dialog_context
    )
    
    return response
