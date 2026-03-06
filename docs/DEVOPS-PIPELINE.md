# Инструкция для DevOps: пайплайн Meet Recordings

Описан пайплайн для **самохостированного GitLab**. Без GitHub, без линтеров (flake8/ruff) и без автотестов — только деплой на сервер при пуше в `develop`.

---

## Обзор

- **Проект:** Django (Meet Recordings), Docker Compose (web, poller, db, ocr в prod).
- **Деплой:** упаковка в tarball → копирование на сервер по SSH → распаковка в `/opt/meetrec` → `docker compose build --no-cache` + `up -d`.
- **Сервер:** приложение слушает на `127.0.0.1:8003`, снаружи — Nginx (HTTPS, домен baza.business-pad.com).

Цель: при пуше в ветку `develop` автоматически разворачивать приложение на сервере тем же способом, что и скрипт `deploy.sh` (включая очистку старых контейнеров до сборки и сборку без кеша).

---

## Что подготовить

### 1. Переменные CI в GitLab

В репозитории: **Settings → CI/CD → Variables** (или в группе/инстансе, если переменные общие).

| Переменная    | Значение        | Protected | Masked |
|---------------|-----------------|-----------|--------|
| `DEPLOY_HOST` | IP или host сервера (напр. 185.245.106.80) | по желанию | Нет |
| `DEPLOY_USER` | SSH-пользователь (напр. root)              | по желанию | Нет |
| `DEPLOY_PASS` | Пароль для SSH                              | по желанию | **Да** |

Для старого GitLab: если нет опции "Mask variable", просто включите "Protected", чтобы пароль не светился в логах обычных пайплайнов.

### 2. На сервере

- Установлены: **Docker**, **Docker Compose** (v2: `docker compose`).
- В каталоге деплоя (по умолчанию `/opt/meetrec`) уже есть файл **`.env`** с продакшен-настройками (SECRET_KEY, S3, OCR, CSRF_TRUSTED_ORIGINS, ALLOWED_HOSTS и т.д.). Пайплайн этот файл не перезаписывает, только дописывает отсутствующие переменные.
- Доступ по SSH по паролю для пользователя `DEPLOY_USER` (либо по ключу — тогда см. вариант с SSH-ключом ниже).

---

## Этапы пайплайна

Один этап — **deploy**:

1. Checkout репозитория.
2. Сборка tarball (исключены `.git`, `.env`, `.venv`, `__pycache__`, media, staticfiles и т.п. — как в `deploy.sh`).
3. Копирование архива на сервер через `scp` (с помощью `sshpass` и пароля из переменной `DEPLOY_PASS`).
4. По SSH на сервере — в том же порядке, что и в `deploy.sh`:
   - распаковка архива, удаление архива;
   - дописка .env при необходимости;
   - `systemctl enable docker`;
   - **очистка до доставки кода:** удаление старых контейнеров meetrec (`docker ps -aq -f name=meetrec | xargs -r docker rm -f`);
   - сборка образов **без кеша** (`build --no-cache web poller`), затем `up -d`;
   - ожидание старта, вывод статуса контейнеров, проверка HTTP, при необходимости — применение nginx.

Деплой запускается только при пуше в ветку **develop**.

---

## Файл `.gitlab-ci.yml`

В корне репозитория создан файл **`.gitlab-ci.yml`** со следующим содержимым (адаптирован под старый GitLab: один job, образ с `openssh-client` и `sshpass`).

```yaml
stages:
  - deploy

variables:
  REMOTE_DIR: /opt/meetrec

deploy:
  stage: deploy
  image: alpine:latest
  only:
    - develop
  before_script:
    - apk add --no-cache openssh-client sshpass
    - mkdir -p ~/.ssh && chmod 700 ~/.ssh
    - ssh-keyscan -H "$DEPLOY_HOST" >> ~/.ssh/known_hosts 2>/dev/null || true
  script:
    # Упаковка архива — те же исключения, что в deploy.sh
    - |
      GZIP=-1 tar --exclude='.git' --exclude='.env' --exclude='deploy' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='.venv' --exclude='venv' \
        --exclude='db.sqlite3' --exclude='media' --exclude='staticfiles' --exclude='*.tar.gz' \
        -czf meetrec-deploy.tar.gz .
    - sshpass -p "$DEPLOY_PASS" scp -o StrictHostKeyChecking=no meetrec-deploy.tar.gz "${DEPLOY_USER}@${DEPLOY_HOST}:/tmp/meetrec-deploy.tar.gz"
    # На сервере — последовательность как в deploy.sh: распаковка, очистка старых контейнеров, сборка без кеша, up
    - |
      sshpass -p "$DEPLOY_PASS" ssh -o StrictHostKeyChecking=no "${DEPLOY_USER}@${DEPLOY_HOST}" "
        set -e
        mkdir -p $REMOTE_DIR && cd $REMOTE_DIR
        tar -xzf /tmp/meetrec-deploy.tar.gz -C .
        rm -f /tmp/meetrec-deploy.tar.gz
        grep -q 'CSRF_TRUSTED_ORIGINS' .env 2>/dev/null || echo 'CSRF_TRUSTED_ORIGINS=https://baza.business-pad.com' >> .env
        grep -q 'ALLOWED_HOSTS' .env 2>/dev/null || echo 'ALLOWED_HOSTS=baza.business-pad.com,185.245.106.80,localhost' >> .env
        grep -q 'OCR_API_URL' .env 2>/dev/null || echo 'OCR_API_URL=http://ocr:8001/ocr' >> .env
        grep -q '^OCR_API_KEY=' .env 2>/dev/null || echo 'OCR_API_KEY=' >> .env
        systemctl enable docker 2>/dev/null || true
        echo 'Удаление старых контейнеров meetrec (очистка до сборки)...'
        docker ps -aq -f name=meetrec | xargs -r docker rm -f 2>/dev/null || true
        echo 'Сборка образов (--no-cache для web/poller)...'
        if docker compose version &>/dev/null; then
          docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache web poller
          docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
        else
          docker-compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache web poller
          docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
        fi
        echo 'Ожидание старта (15 сек)...'
        sleep 15
        docker ps -a --filter name=meetrec --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
        HTTP_CODE=\$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: localhost' http://127.0.0.1:8003/login/ 2>/dev/null || echo '000')
        echo \"Проверка http://127.0.0.1:8003/login/ -> HTTP \$HTTP_CODE\"
        if [ \"\$HTTP_CODE\" != '200' ] && [ \"\$HTTP_CODE\" != '302' ]; then
          docker logs meetrec_web_1 2>&1 | tail -50
        fi
        if [ -f deploy/nginx-baza.conf ]; then
          if [ -d /etc/nginx/sites-available ]; then
            cp deploy/nginx-baza.conf /etc/nginx/sites-available/baza.business-pad.com
            ln -sf /etc/nginx/sites-available/baza.business-pad.com /etc/nginx/sites-enabled/ 2>/dev/null || true
          elif [ -d /etc/nginx/conf.d ]; then
            cp deploy/nginx-baza.conf /etc/nginx/conf.d/baza.conf
          fi
          nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
        fi
      "
```

Переменные `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PASS` должны быть заданы в **Settings → CI/CD → Variables**. На старом GitLab убедитесь, что переменные не помечены как "Environment scope" с ограничением, если не используете окружения.

---

## Вариант: деплой по SSH-ключу (без пароля)

Если не хотите хранить пароль в переменных GitLab:

1. Сгенерировать ключ для CI:  
   `ssh-keygen -t ed25519 -C "gitlab-deploy" -f deploy_key -N ""`
2. Публичный ключ `deploy_key.pub` добавить на сервер в `~/.ssh/authorized_keys` пользователя деплоя.
3. В GitLab CI/CD Variables добавить переменную **`SSH_PRIVATE_KEY`** (Type: File или Variable), значение — содержимое файла `deploy_key` (приватный ключ целиком). Включить **Mask variable**, если есть такая опция.

В `.gitlab-ci.yml` в job `deploy` заменить `before_script` и блок `script` на:

```yaml
  before_script:
    - apk add --no-cache openssh-client
    - mkdir -p ~/.ssh && chmod 700 ~/.ssh
    - echo "$SSH_PRIVATE_KEY" > ~/.ssh/deploy_key && chmod 600 ~/.ssh/deploy_key
    - ssh-keyscan -H "$DEPLOY_HOST" >> ~/.ssh/known_hosts 2>/dev/null || true
  script:
    - |
      GZIP=-1 tar --exclude='.git' --exclude='.env' --exclude='__pycache__' \
        --exclude='.venv' --exclude='venv' --exclude='db.sqlite3' \
        --exclude='media' --exclude='staticfiles' --exclude='*.tar.gz' \
        -czf meetrec-deploy.tar.gz .
    - scp -i ~/.ssh/deploy_key -o StrictHostKeyChecking=no meetrec-deploy.tar.gz "${DEPLOY_USER}@${DEPLOY_HOST}:/tmp/meetrec-deploy.tar.gz"
    - |
      ssh -i ~/.ssh/deploy_key -o StrictHostKeyChecking=no "${DEPLOY_USER}@${DEPLOY_HOST}" "
        set -e
        mkdir -p $REMOTE_DIR && cd $REMOTE_DIR
        tar -xzf /tmp/meetrec-deploy.tar.gz -C .
        rm -f /tmp/meetrec-deploy.tar.gz
        grep -q 'CSRF_TRUSTED_ORIGINS' .env 2>/dev/null || echo 'CSRF_TRUSTED_ORIGINS=https://baza.business-pad.com' >> .env
        grep -q 'ALLOWED_HOSTS' .env 2>/dev/null || echo 'ALLOWED_HOSTS=baza.business-pad.com,185.245.106.80,localhost' >> .env
        grep -q 'OCR_API_URL' .env 2>/dev/null || echo 'OCR_API_URL=http://ocr:8001/ocr' >> .env
        grep -q '^OCR_API_KEY=' .env 2>/dev/null || echo 'OCR_API_KEY=' >> .env
        systemctl enable docker 2>/dev/null || true
        docker ps -aq -f name=meetrec | xargs -r docker rm -f 2>/dev/null || true
        if docker compose version &>/dev/null; then
          docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache web poller
          docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
        else
          docker-compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache web poller
          docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
        fi
        sleep 15
        docker ps -a --filter name=meetrec --format 'table {{.Names}}\t{{.Status}}'
        if [ -f deploy/nginx-baza.conf ]; then
          [ -d /etc/nginx/sites-available ] && cp deploy/nginx-baza.conf /etc/nginx/sites-available/baza.business-pad.com && ln -sf /etc/nginx/sites-available/baza.business-pad.com /etc/nginx/sites-enabled/ 2>/dev/null || true
          [ -d /etc/nginx/conf.d ] && cp deploy/nginx-baza.conf /etc/nginx/conf.d/baza.conf
          nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
        fi
      "
```

Установку `sshpass` и использование `DEPLOY_PASS` убрать. Переменная `DEPLOY_PASS` больше не нужна.

---

## Совместимость со старым GitLab

- Используется синтаксис **`only: - develop`** вместо `rules`, чтобы пайплайн работал в старых версиях GitLab.
- Один job, один stage — без Docker-in-Docker и без дополнительных образов.
- Если в вашей версии нет `docker compose` (v2), на сервере может быть `docker-compose` (v1). Тогда на **сервере** в скрипте деплоя замените `docker compose` на `docker-compose` (через дефис). В самом `.gitlab-ci.yml` мы только вызываем команды по SSH — выполняются они уже на сервере, поэтому достаточно привести в соответствие команды там.

---

## Чек-лист для девопса

- [ ] В GitLab: **Settings → CI/CD → Variables** — задать `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PASS` (или `SSH_PRIVATE_KEY` при деплое по ключу).
- [ ] На сервере: в `REMOTE_DIR` (по умолчанию `/opt/meetrec`) есть рабочий `.env` с продакшен-настройками.
- [ ] На сервере установлены Docker и Docker Compose (или docker-compose v1), по SSH возможен вход под `DEPLOY_USER`.
- [ ] В корне репозитория лежит `.gitlab-ci.yml` (из этой инструкции или из репозитория).
- [ ] После первого успешного пайплайна проверить https://baza.business-pad.com/ и при необходимости логи контейнеров на сервере (`docker compose logs -f web`).
