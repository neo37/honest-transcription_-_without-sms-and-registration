import functools
from django.shortcuts import redirect
from django.urls import reverse


SMARTY_DOMAINS = {'smarty.rest', 'www.smarty.rest'}

def site_login_required(view_func):
    """Decorator: require session login via session['user_id']."""
    @functools.wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.session.get('user_id'):
            return view_func(request, *args, **kwargs)
        from urllib.parse import urlencode
        next_url = request.get_full_path()
        host = request.get_host().split(':')[0]
        if host in SMARTY_DOMAINS:
            login_name = 'recordings:smarty_login'
        else:
            login_name = 'recordings:login'
        login_url = reverse(login_name) + '?' + urlencode({'next': next_url})
        return redirect(login_url)
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
