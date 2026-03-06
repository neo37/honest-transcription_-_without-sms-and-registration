import functools
from django.shortcuts import redirect
from django.urls import reverse


def site_login_required(view_func):
    """Decorator: require session login via session['user_id']."""
    @functools.wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.session.get('user_id'):
            return view_func(request, *args, **kwargs)
        return redirect(reverse('recordings:login'))
    return wrapped


def get_current_user(request):
    """Вернуть SiteUser по session['user_id'] или None."""
    uid = request.session.get('user_id')
    if not uid:
        return None
    from .models import SiteUser
    return SiteUser.objects.select_related('space').filter(pk=uid).first()


# Kept for backwards compatibility (used in _log_access via session)
def check_site_credentials(username, password):
    from django.conf import settings
    u = (username or '').strip()
    p = (password or '').strip()
    expected_u = (getattr(settings, 'SITE_LOGIN_USERNAME', '') or '').strip()
    expected_p = (getattr(settings, 'SITE_LOGIN_PASSWORD', '') or '').strip()
    return u == expected_u and p == expected_p
