# Инструкция по настройке автодеплоя

## Что было настроено

1. ✅ GitHub Actions workflow ()
2. ✅ Скрипт деплоя ()
3. ✅ Файл зависимостей ()

## Настройка SSH ключа в GitHub Secrets

Для работы автодеплоя нужно добавить SSH приватный ключ сервера в GitHub Secrets.

### Шаг 1: Получите приватный SSH ключ

На сервере выполните:
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACA5VAnbcOJs7IpNiUccwJKadB/7qznRgA7wNDDPItUKIAAAAKCnutsfp7rb
HwAAAAtzc2gtZWQyNTUxOQAAACA5VAnbcOJs7IpNiUccwJKadB/7qznRgA7wNDDPItUKIA
AAAECPYkJkGeGYpHrfyJH6uKHV7amaFxOUnTSPbhGOe94o7jlUCdtw4mzsik2JRxzAkpp0
H/urOdGADvA0MM8i1QogAAAAFnlvdXItZW1haWxAZXhhbXBsZS5jb20BAgMEBQYH
-----END OPENSSH PRIVATE KEY-----

Или используйте уже полученный ключ:


### Шаг 2: Добавьте ключ в GitHub Secrets

1. Перейдите в репозиторий на GitHub
2. Откройте **Settings** → **Secrets and variables** → **Actions**
3. Нажмите **New repository secret**
4. Имя: 
5. Значение: вставьте весь приватный ключ (включая  и )
6. Нажмите **Add secret**

### Шаг 3: Проверка

После добавления секрета:
1. Сделайте любой коммит в  ветку
2. Перейдите в **Actions** в репозитории
3. Должен запуститься workflow Deploy to Server
4. Проверьте логи выполнения

## Что делает скрипт деплоя

 выполняет следующие действия:

1. **git pull** - получает последние изменения из репозитория
2. **Активация окружения** - активирует виртуальное окружение Python
3. **Установка зависимостей** - обновляет пакеты из 
4. **Миграции БД** - выполняет миграции Django
5. **Сбор статики** - собирает статические файлы
6. **Перезапуск сервисов** - перезапускает Gunicorn и перезагружает Nginx

## Ручной запуск деплоя

Если нужно запустить деплой вручную на сервере:



## Отладка

### Проверка логов GitHub Actions

1. Перейдите в **Actions** в репозитории
2. Выберите последний workflow run
3. Откройте job deploy
4. Проверьте логи выполнения

### Проверка на сервере

-- No entries --

## Безопасность

⚠️ **Важно**: 
- SSH приватный ключ хранится в GitHub Secrets (зашифрован)
- Не коммитьте приватные ключи в репозиторий
- Регулярно обновляйте SSH ключи
- Используйте отдельный ключ только для деплоя (рекомендуется)

## Альтернативный вариант: отдельный ключ для деплоя

Для большей безопасности можно создать отдельный SSH ключ только для деплоя:

Generating public/private ed25519 key pair.
Your identification has been saved in /home/n36/.ssh/id_ed25519_deploy
Your public key has been saved in /home/n36/.ssh/id_ed25519_deploy.pub
The key fingerprint is:
SHA256:ZpHbB6p7Bden0HAbkbFmC92Fg1oeI9iMgs7i0bzlqzo deploy@github-actions
The key's randomart image is:
+--[ED25519 256]--+
|     .   =  o= ..|
|    . . o.=.Ooo. |
|   =   .o oX==.. |
|  o = . .=+==..  |
| . o +  So..oo   |
|  . . .+  ...    |
|      .. .       |
|  E   ...        |
|  .o....         |
+----[SHA256]-----+
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACBcSrjkyBc68jgOl96E0VsA+Wmhf1/n5blImKYTlO1GsgAAAJi0/FdntPxX
ZwAAAAtzc2gtZWQyNTUxOQAAACBcSrjkyBc68jgOl96E0VsA+Wmhf1/n5blImKYTlO1Gsg
AAAEC2WVBAL5bCx3bqCaznyU3dsUAVNslOigYn2MTkoo/tbFxKuOTIFzryOA6X3oTRWwD5
aaF/X+fluUiYphOU7UayAAAAFWRlcGxveUBnaXRodWItYWN0aW9ucw==
-----END OPENSSH PRIVATE KEY-----

Затем обновите workflow, чтобы использовать другой ключ или ограничьте права этого ключа.

