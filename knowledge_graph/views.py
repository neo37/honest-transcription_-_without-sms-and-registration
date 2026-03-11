from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import Q

from recordings.auth_backend import site_login_required, get_current_user
from recordings.models import Space
from .models import KGNode, KGEdge


def _space_filter(user):
    if not user or not user.space:
        return Q(space__isnull=True)
    from django.conf import settings
    bp_slug = getattr(settings, 'BP_SPACE_SLUG', 'org-bp')
    if user.space.slug == bp_slug:
        return Q()  # BP видит все
    return Q(space=user.space)


@site_login_required
def graph_page(request):
    user = get_current_user(request)
    node_types = KGNode.NodeType.choices
    spaces = []
    if user and user.space:
        spaces = [user.space]
    return render(request, 'knowledge_graph/graph.html', {
        'node_types': node_types,
    })


@site_login_required
def graph_data(request):
    """JSON для D3: {nodes: [...], links: [...]}"""
    user = get_current_user(request)
    sf = _space_filter(user)

    # Фильтры из GET
    type_filter = request.GET.get('type', '')
    search = request.GET.get('q', '').strip()
    limit = min(int(request.GET.get('limit', 200)), 500)

    nodes_qs = KGNode.objects.filter(sf).order_by('-weight')
    if type_filter:
        nodes_qs = nodes_qs.filter(node_type=type_filter)
    if search:
        nodes_qs = nodes_qs.filter(title__icontains=search)
    nodes_qs = nodes_qs[:limit]

    node_ids = {n.pk for n in nodes_qs}

    edges_qs = KGEdge.objects.filter(
        source_id__in=node_ids, target_id__in=node_ids,
    ).select_related('source', 'target').order_by('-weight')[:limit * 3]

    TYPE_COLOR = {
        'person':    '#4f8ef7',
        'topic':     '#f7a84f',
        'org':       '#7bc47b',
        'decision':  '#e05c5c',
        'task':      '#c47bbc',
        'recording': '#5cd3d3',
        'wiki':      '#d3c55c',
    }

    nodes_out = []
    for n in nodes_qs:
        rec_id = n.recording_id
        wiki_slug = n.wiki_article.slug if n.wiki_article else None
        nodes_out.append({
            'id': n.pk,
            'label': n.title[:60],
            'type': n.node_type,
            'color': TYPE_COLOR.get(n.node_type, '#aaa'),
            'weight': n.weight,
            'rec_id': rec_id,
            'wiki_slug': wiki_slug,
        })

    links_out = []
    for e in edges_qs:
        links_out.append({
            'source': e.source_id,
            'target': e.target_id,
            'type': e.relation_type,
            'weight': e.weight,
        })

    stats = {
        'total_nodes': KGNode.objects.filter(sf).count(),
        'total_edges': KGEdge.objects.filter(
            source__in=KGNode.objects.filter(sf)
        ).count(),
    }

    return JsonResponse({'nodes': nodes_out, 'links': links_out, 'stats': stats})


@site_login_required
def rebuild_graph(request):
    """POST — перестроить граф для пространства пользователя."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    user = get_current_user(request)
    space = user.space if user else None

    from .models import KGNode, KGEdge
    from .extractor import extract_recording, extract_wiki
    from recordings.models import Recording
    from wiki_kb.models import WikiArticle

    # Очищаем граф пространства
    KGEdge.objects.filter(source__space=space).delete()
    KGNode.objects.filter(space=space).delete()

    recs = Recording.objects.filter(space=space).exclude(transcription='').exclude(transcription__isnull=True)
    arts = WikiArticle.objects.filter(space=space, is_deleted=False)

    nodes = 0
    for rec in recs:
        try:
            nodes += extract_recording(rec)
        except Exception:
            pass
    for art in arts:
        try:
            nodes += extract_wiki(art)
        except Exception:
            pass

    from .models import KGEdge as E
    return JsonResponse({
        'ok': True,
        'nodes': KGNode.objects.filter(space=space).count(),
        'edges': E.objects.filter(source__space=space).count(),
    })
