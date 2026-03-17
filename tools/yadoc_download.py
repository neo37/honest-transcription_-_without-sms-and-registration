#!/usr/bin/env python3
"""
Скачивает страницы из Яндекс Вики в папку с Markdown-файлами.

Использование:
    python yadoc_download.py --token YOUR_OAUTH_TOKEN --org-id YOUR_ORG_ID

Получить OAuth-токен:
    https://oauth.yandex.ru/authorize?response_type=token&client_id=5cf2e269eb234741ab7a3f2bd5f0bc71

Получить Org-ID:
    https://admin.yandex.ru/ → раздел "О компании" → ID организации
    или: curl -H "Authorization: OAuth TOKEN" https://api360.yandex.net/directory/v1/org/ | python3 -m json.tool

Опции:
    --token       OAuth-токен Яндекса (обязательно)
    --org-id      ID организации в Яндекс 360 (обязательно)
    --slug        Скачать только одну страницу по slug (напр. /users/ivan/meeting-notes)
    --root        Скачать страницу и все дочерние страницы по корневому slug
    --out         Папка для сохранения (по умолчанию: ./yadoc_out)
    --limit       Максимум страниц за раз при листинге (по умолчанию: 100)
    --all         Скачать все страницы организации

Примеры:
    # Все страницы организации
    python yadoc_download.py --token oAuth... --org-id 123456 --all

    # Конкретная страница
    python yadoc_download.py --token oAuth... --org-id 123456 --slug /users/ivan/mypage

    # Страница + всё дерево дочерних
    python yadoc_download.py --token oAuth... --org-id 123456 --root /teams/backend
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Установите requests: pip install requests")
    sys.exit(1)


BASE_URL = "https://api.wiki.yandex.net/v1"


class YandexWiki:
    def __init__(self, token: str, org_id: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"OAuth {token}",
            "X-Org-Id": str(org_id),
        })

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params)
        if resp.status_code == 401:
            print("❌ Ошибка авторизации. Проверьте токен и Org-ID.")
            sys.exit(1)
        if resp.status_code == 403:
            print(f"❌ Нет доступа к {path}. Проверьте права пользователя.")
            return {}
        if resp.status_code == 404:
            print(f"⚠️  Не найдено: {path}")
            return {}
        resp.raise_for_status()
        return resp.json()

    def get_page(self, page_id: str = None, slug: str = None) -> dict:
        """Получить страницу по ID или slug."""
        if page_id:
            return self._get(f"/pages/{page_id}")
        if slug:
            # slug → сначала найдём через listing
            data = self._get("/pages", params={"slug": slug, "fields": "id,slug,title,body,parentId,updatedAt"})
            pages = data.get("pages", [])
            if pages:
                return pages[0]
            return {}
        return {}

    def get_page_body(self, page_id: str) -> str:
        """Получить содержимое страницы в формате wiki-разметки."""
        data = self._get(f"/pages/{page_id}", params={"fields": "body"})
        return data.get("body", "")

    def list_pages(self, root_slug: str = None) -> list[dict]:
        """Получить список страниц. Если задан root_slug — только его дерево."""
        all_pages = []
        page_token = None
        params = {
            "fields": "id,slug,title,parentId,updatedAt",
            "limit": 100,
        }
        if root_slug:
            params["rootSlug"] = root_slug

        print("⏳ Получаю список страниц...", end="", flush=True)
        while True:
            if page_token:
                params["pageToken"] = page_token
            data = self._get("/pages", params=params)
            if not data:
                break
            pages = data.get("pages", [])
            all_pages.extend(pages)
            print(f"\r⏳ Получено страниц: {len(all_pages)}", end="", flush=True)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
            time.sleep(0.1)  # не DDoS-им API

        print(f"\r✅ Найдено страниц: {len(all_pages)}          ")
        return all_pages

    def get_children(self, page_id: str) -> list[dict]:
        """Дочерние страницы для данной страницы."""
        data = self._get(f"/pages/{page_id}/children", params={"fields": "id,slug,title,parentId"})
        return data.get("pages", [])


def slug_to_filepath(slug: str, out_dir: Path) -> Path:
    """Превращает slug вроде /users/ivan/meeting-notes в файл out_dir/users/ivan/meeting-notes.md"""
    # Убираем ведущий слеш
    relative = slug.lstrip("/")
    # Очищаем от недопустимых символов в именах файлов
    relative = re.sub(r'[<>:"|?*]', '_', relative)
    path = out_dir / (relative + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def save_page(wiki: YandexWiki, page: dict, out_dir: Path, index: int = None, total: int = None) -> None:
    page_id = page.get("id")
    slug = page.get("slug", page_id)
    title = page.get("title", "Без названия")

    prefix = f"[{index}/{total}] " if index and total else ""
    print(f"{prefix}📄 {slug}")

    body = wiki.get_page_body(page_id)
    if not body:
        body = "_Содержимое страницы недоступно или пусто._"

    filepath = slug_to_filepath(slug, out_dir)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"<!-- slug: {slug} -->\n")
        if page.get("updatedAt"):
            f.write(f"<!-- updated: {page['updatedAt']} -->\n")
        f.write("\n")
        f.write(body)

    time.sleep(0.05)  # пауза между запросами тела страниц


def download_all(wiki: YandexWiki, out_dir: Path, root_slug: str = None) -> None:
    pages = wiki.list_pages(root_slug=root_slug)
    if not pages:
        print("⚠️  Страниц не найдено.")
        return

    total = len(pages)
    for i, page in enumerate(pages, 1):
        save_page(wiki, page, out_dir, index=i, total=total)

    print(f"\n✅ Готово. Сохранено {total} страниц в {out_dir}/")


def download_single(wiki: YandexWiki, slug: str, out_dir: Path) -> None:
    page = wiki.get_page(slug=slug)
    if not page:
        print(f"❌ Страница не найдена: {slug}")
        return
    save_page(wiki, page, out_dir)
    print(f"\n✅ Сохранено в {slug_to_filepath(slug, out_dir)}")


def download_tree(wiki: YandexWiki, root_slug: str, out_dir: Path) -> None:
    """Скачать страницу и всё дерево дочерних рекурсивно."""
    print(f"🌳 Скачиваю дерево от: {root_slug}")
    download_all(wiki, out_dir, root_slug=root_slug)


def main():
    parser = argparse.ArgumentParser(description="Скачать страницы из Яндекс Вики")
    parser.add_argument("--token", required=True, help="OAuth-токен Яндекса")
    parser.add_argument("--org-id", required=True, help="ID организации Яндекс 360")
    parser.add_argument("--slug", help="Скачать одну страницу по slug")
    parser.add_argument("--root", help="Скачать страницу и всё дерево дочерних")
    parser.add_argument("--all", action="store_true", help="Скачать все страницы организации")
    parser.add_argument("--out", default="./yadoc_out", help="Папка для сохранения (по умолчанию: ./yadoc_out)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    wiki = YandexWiki(token=args.token, org_id=args.org_id)

    if args.slug:
        download_single(wiki, args.slug, out_dir)
    elif args.root:
        download_tree(wiki, args.root, out_dir)
    elif args.all:
        download_all(wiki, out_dir)
    else:
        parser.print_help()
        print("\n💡 Укажите --all, --root /slug или --slug /slug")
        sys.exit(1)


if __name__ == "__main__":
    main()
