from .auth_backend import get_current_user


def site_user(request):
    """Inject current_user, user_space_slug, user_tz, unread_dms into all template contexts."""
    user = get_current_user(request)
    unread_dms = 0
    if user:
        try:
            from .models import DirectMessage
            unread_dms = DirectMessage.objects.filter(recipient=user, read_at__isnull=True).count()
        except Exception:
            pass
    return {
        'current_user': user,
        'user_space_slug': user.space.slug if (user and user.space) else '',
        'user_tz': user.timezone if user else 'Europe/Moscow',
        'unread_dms': unread_dms,
    }
