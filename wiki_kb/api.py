"""
Wiki REST API — Basic auth по wiki_username:wiki_password пространства

Эндпоинты:
  GET    /api/wiki/                     — список всех статей
  GET    /api/wiki/tree/                — вложенная структура с корня
  GET    /api/wiki/tree/<slug>/         — вложенная структура от статьи
  GET    /api/wiki/search/?q=...        — семантический поиск
  POST   /api/wiki/                     — создать статью
  GET    /api/wiki/<slug>/              — читать статью
  PUT    /api/wiki/<slug>/              — редактировать статью
  DELETE /api/wiki/<slug>/              — удалить статью
  POST   /api/wiki/<slug>/move/         — переместить статью (сменить родителя)
"""
from __future__ import annotations

import base64
import json

from django.http import JsonResponse
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt

from recordings.models import Space
from .models import WikiArticle, WikiRevision


# ── Auth ──────────────────────────────────────────────────────────────────────

def _resolve_space(request) -> Space | None:
    """
    Извлекает Basic-auth заголовок и возвращает Space, у которой
    wiki_username и wiki_password совпадают. Иначе None.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode()
        username, password = decoded.split(":", 1)
    except Exception:
        return None

    return Space.objects.filter(
        wiki_username=username,
        wiki_password=password,
    ).exclude(wiki_username="").exclude(wiki_password="").first()


def _forbidden():
    resp = JsonResponse({"error": "Unauthorized"}, status=401)
    resp["WWW-Authenticate"] = 'Basic realm="wiki"'
    return resp


def _article_dict(art: WikiArticle, include_content=True) -> dict:
    d = {
        "id": art.pk,
        "slug": art.slug,
        "title": art.title,
        "parent_slug": art.parent.slug if art.parent else None,
        "parent_id": art.parent_id,
        "order": art.order,
        "created_at": art.created_at.isoformat(),
        "updated_at": art.updated_at.isoformat(),
    }
    if include_content:
        d["content"] = art.content
    return d


def _tree_node(art: WikiArticle) -> dict:
    node = _article_dict(art, include_content=False)
    node["children"] = [_tree_node(c) for c in art.get_children().filter(is_personal=False)]
    return node


def _space_qs(space: Space):
    """Queryset статей пространства (только не удалённые и не персональные)."""
    return WikiArticle.objects.filter(is_deleted=False, space=space, is_personal=False)


# ── Auth decorator ────────────────────────────────────────────────────────────
def wiki_api_auth(fn):
    def wrapper(request, *args, **kwargs):
        space = _resolve_space(request)
        if space is None:
            return _forbidden()
        return fn(request, space, *args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


# ── /api/wiki/ ────────────────────────────────────────────────────────────────
@csrf_exempt
@wiki_api_auth
def api_wiki_list(request, space):
    """
    GET  — плоский список всех статей (без контента)
    POST — создать статью
    """
    if request.method == "GET":
        arts = _space_qs(space).order_by("order", "title")
        return JsonResponse({"articles": [_article_dict(a, include_content=False) for a in arts]})

    if request.method == "POST":
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "bad json"}, status=400)

        title = data.get("title", "").strip()
        if not title:
            return JsonResponse({"error": "title required"}, status=400)

        content = data.get("content", "")
        order = int(data.get("order", 0))

        # slug
        base_slug = data.get("slug") or slugify(title) or "wiki-page"
        slug = base_slug
        n = 1
        while WikiArticle.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{n}"
            n += 1

        # parent — только внутри того же пространства
        parent = None
        parent_slug = data.get("parent_slug")
        parent_id = data.get("parent_id")
        if parent_slug:
            parent = _space_qs(space).filter(slug=parent_slug).first()
        elif parent_id:
            parent = _space_qs(space).filter(pk=parent_id).first()

        art = WikiArticle.objects.create(
            title=title, slug=slug, content=content,
            parent=parent, order=order, space=space,
        )
        WikiRevision.objects.create(article=art, content=content, comment="Создано через API")
        return JsonResponse(_article_dict(art), status=201)

    return JsonResponse({"error": "method not allowed"}, status=405)


# ── /api/wiki/tree/ ───────────────────────────────────────────────────────────
@csrf_exempt
@wiki_api_auth
def api_wiki_tree(request, space):
    """GET — вложенная структура с корня."""
    roots = _space_qs(space).filter(parent__isnull=True).order_by("order", "title")
    return JsonResponse({"tree": [_tree_node(r) for r in roots]})


# ── /api/wiki/tree/<slug>/ ────────────────────────────────────────────────────
@csrf_exempt
@wiki_api_auth
def api_wiki_tree_from(request, space, slug):
    """GET — вложенная структура начиная от указанной статьи (включая её)."""
    art = _space_qs(space).filter(slug=slug).first()
    if not art:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({"tree": _tree_node(art)})


# ── /api/wiki/<slug>/ ─────────────────────────────────────────────────────────
@csrf_exempt
@wiki_api_auth
def api_wiki_detail(request, space, slug):
    """
    GET    — читать статью
    PUT    — редактировать статью
    DELETE — удалить статью
    """
    art = _space_qs(space).filter(slug=slug).first()
    if not art:
        return JsonResponse({"error": "not found"}, status=404)

    if request.method == "GET":
        d = _article_dict(art)
        d["ancestors"] = [{"slug": a.slug, "title": a.title} for a in art.get_ancestors()]
        return JsonResponse(d)

    if request.method in ("PUT", "PATCH"):
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({"error": "bad json"}, status=400)

        changed = []
        if "title" in data:
            art.title = data["title"].strip()
            changed.append("title")
        if "content" in data:
            art.content = data["content"]
            changed.append("content")
            WikiRevision.objects.create(
                article=art, content=art.content,
                comment=data.get("comment", "Изменено через API"),
            )
        if "order" in data:
            art.order = int(data["order"])
            changed.append("order")
        if changed:
            changed.append("updated_at")
            art.save(update_fields=changed)
        return JsonResponse(_article_dict(art))

    if request.method == "DELETE":
        art.is_deleted = True
        art.save(update_fields=["is_deleted"])
        return JsonResponse({"ok": True, "deleted": slug})

    return JsonResponse({"error": "method not allowed"}, status=405)


# ── /api/wiki/<slug>/move/ ────────────────────────────────────────────────────
@csrf_exempt
@wiki_api_auth
def api_wiki_move(request, space, slug):
    """
    POST {parent_slug: "..."} или {parent_id: 123} или {parent_slug: null} → в корень
    """
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)

    art = _space_qs(space).filter(slug=slug).first()
    if not art:
        return JsonResponse({"error": "not found"}, status=404)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "bad json"}, status=400)

    new_parent = None
    if "parent_slug" in data:
        if data["parent_slug"]:
            new_parent = _space_qs(space).filter(slug=data["parent_slug"]).first()
            if not new_parent:
                return JsonResponse({"error": "parent not found"}, status=404)
    elif "parent_id" in data:
        if data["parent_id"]:
            new_parent = _space_qs(space).filter(pk=data["parent_id"]).first()
            if not new_parent:
                return JsonResponse({"error": "parent not found"}, status=404)

    # Проверяем цикл
    if new_parent:
        desc_ids = art.get_all_descendants_ids()
        if new_parent.pk in desc_ids or new_parent.pk == art.pk:
            return JsonResponse({"error": "cannot move into own descendant"}, status=400)

    art.parent = new_parent
    art.save(update_fields=["parent"])
    return JsonResponse(_article_dict(art))


# ── /api/wiki/search/?q=... ───────────────────────────────────────────────────
@csrf_exempt
@wiki_api_auth
def api_wiki_search(request, space):
    """GET ?q=... — семантический поиск по статьям пространства."""
    if request.method != "GET":
        return JsonResponse({"error": "method not allowed"}, status=405)

    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"error": "q required"}, status=400)

    top_k = min(int(request.GET.get("top", 5)), 20)

    from .models import wiki_semantic_search
    results = wiki_semantic_search(query, space=space, top_k=top_k)

    return JsonResponse({
        "query": query,
        "results": [
            {
                "slug": r["article"].slug,
                "title": r["article"].title,
                "score": round(r["score"], 4),
                "excerpt": r["excerpt"],
            }
            for r in results
        ],
    })
