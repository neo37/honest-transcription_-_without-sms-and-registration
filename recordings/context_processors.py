from .auth_backend import get_current_user


def site_user(request):
    """Inject current_user and user_space_slug into all template contexts."""
    user = get_current_user(request)
    return {
        'current_user': user,
        'user_space_slug': user.space.slug if (user and user.space) else '',
    }
