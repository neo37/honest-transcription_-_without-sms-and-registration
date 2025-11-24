FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создание рабочей директории
WORKDIR /app

# Копирование requirements
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Установка curl для healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Копирование кода приложения
COPY . /app/

# Создание директорий для медиа и статики
RUN mkdir -p /app/media/screenshots /app/staticfiles

# Установка прав
RUN chmod +x /app/manage.py

# Порт для приложения
EXPOSE 8000

# Команда по умолчанию (переопределяется в docker-compose)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

