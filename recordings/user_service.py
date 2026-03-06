from django.conf import settings
from django.utils import timezone

from .models import Space, SiteUser
from .email_service import generate_password, send_password_email

BP_EMAILS_LOWER = {e.lower() for e in getattr(settings, 'BP_EMAILS', [])}
BP_DOMAIN = 'core.business-pad.com'


def _is_bp_email(email: str) -> bool:
    """BP-пользователь: любой @core.business-pad.com или явно в BP_EMAILS."""
    e = email.strip().lower()
    return e.endswith('@' + BP_DOMAIN) or e in BP_EMAILS_LOWER


def get_or_create_bp_space() -> Space:
    """Получить или создать BP-пространство."""
    slug = getattr(settings, 'BP_SPACE_SLUG', 'org-bp')
    space, _ = Space.objects.get_or_create(slug=slug, defaults={'name': 'BusinessPad'})
    return space


def get_or_create_user(email: str) -> tuple:
    """
    Найти или создать SiteUser по email.
    Возвращает (user, created).
    BP-email → space=BP, free_left=None.
    Обычный → free_left=5.
    """
    email_lower = email.strip().lower()
    is_bp = _is_bp_email(email_lower)

    try:
        user = SiteUser.objects.get(email__iexact=email_lower)
        return user, False
    except SiteUser.DoesNotExist:
        pass

    if is_bp:
        space = get_or_create_bp_space()
        user = SiteUser.objects.create(
            email=email_lower,
            password='',
            space=space,
            free_left=None,
        )
    else:
        user = SiteUser.objects.create(
            email=email_lower,
            password='',
            space=None,
            free_left=5,
        )
    return user, True


def set_password_no_email(user: SiteUser) -> str | None:
    """
    Для pilot_integration: генерирует и устанавливает пароль без отправки email.
    first_login_at выставляется сразу (пароль показывается на экране).
    Возвращает plain-пароль, или None если пароль уже был установлен.
    """
    if user.first_login_at is not None:
        return None
    pwd = generate_password()
    user.set_password(pwd)
    user.first_login_at = timezone.now()
    user.save(update_fields=['password', 'first_login_at'])
    return pwd


def handle_first_login(user: SiteUser) -> str | None:
    """
    При первом входе (first_login_at is None) генерирует пароль.
    Пытается отправить email. Если отправка успешна — ставит first_login_at.
    Если нет — first_login_at остаётся None (можно повторить попытку).
    Возвращает plain-пароль (для показа на экране), или None если пароль уже был выдан.
    """
    if user.first_login_at is not None:
        return None
    pwd = generate_password()
    user.set_password(pwd)
    user.save(update_fields=['password'])
    sent = send_password_email(user.email, pwd)
    if sent:
        user.first_login_at = timezone.now()
        user.save(update_fields=['first_login_at'])
    # Возвращаем пароль в любом случае — для показа на экране
    return pwd
