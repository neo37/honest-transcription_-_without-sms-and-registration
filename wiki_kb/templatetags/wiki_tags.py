from django import template
from django.db.models import Q

register = template.Library()


@register.filter
def children_for_user(article, user):
    """Дочерние статьи с учётом персонального фильтра текущего пользователя."""
    qs = article.children.filter(is_deleted=False).order_by('order', 'title')
    if user:
        return qs.filter(Q(is_personal=False) | Q(is_personal=True, created_by=user))
    return qs.filter(is_personal=False)
