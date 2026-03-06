import logging
import random
import string

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def generate_password(length=10) -> str:
    """Генерирует случайный пароль из букв и цифр."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(random.choices(alphabet, k=length))


def send_password_email(email: str, password: str) -> bool:
    """Отправляет пароль на email пользователя через SMTP."""
    subject = 'Ваш пароль для BusinessPad MeetRec'
    body = (
        f'Здравствуйте!\n\n'
        f'Вы зарегистрированы в системе записи встреч BusinessPad MeetRec.\n\n'
        f'Ваш пароль: {password}\n\n'
        f'Войдите по ссылке: https://baza.business-pad.com/login/\n\n'
        f'Сохраните этот пароль — он больше не будет выслан повторно.\n\n'
        f'— BusinessPad'
    )
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or getattr(settings, 'EMAIL_HOST_USER', '')
    try:
        send_mail(subject, body, from_email, [email], fail_silently=False)
        return True
    except Exception as e:
        logger.error('Failed to send password email to %s: %s', email, e)
        return False
