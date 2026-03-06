# Generated manually for recordings app

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='PollLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started_at', models.DateTimeField(auto_now_add=True, verbose_name='Начало')),
                ('finished_at', models.DateTimeField(blank=True, null=True, verbose_name='Конец')),
                ('files_found', models.PositiveIntegerField(default=0, verbose_name='Найдено файлов')),
                ('files_stable', models.PositiveIntegerField(default=0, verbose_name='Стабильных')),
                ('files_transcribed', models.PositiveIntegerField(default=0, verbose_name='Транскрибировано')),
                ('message', models.TextField(blank=True, verbose_name='Сообщение')),
                ('success', models.BooleanField(default=True, verbose_name='Успех')),
            ],
            options={
                'verbose_name': 'Лог опроса',
                'verbose_name_plural': 'Логи опросов',
                'ordering': ['-started_at'],
            },
        ),
        migrations.CreateModel(
            name='Recording',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('s3_key', models.CharField(max_length=512, unique=True, verbose_name='Ключ S3')),
                ('filename', models.CharField(db_index=True, max_length=255, verbose_name='Имя файла')),
                ('size_bytes', models.BigIntegerField(default=0, verbose_name='Размер (байт)')),
                ('last_size_check_at', models.DateTimeField(blank=True, null=True, verbose_name='Время последней проверки размера')),
                ('size_stable_since', models.DateTimeField(blank=True, null=True, verbose_name='Размер стабилен с')),
                ('status', models.CharField(choices=[('pending', 'Ожидание (копируется)'), ('stable', 'Готов к транскрибации'), ('transcribing', 'Транскрибируется'), ('done', 'Готово'), ('failed', 'Ошибка')], db_index=True, default='pending', max_length=20, verbose_name='Статус')),
                ('transcription', models.TextField(blank=True, verbose_name='Транскрипция')),
                ('transcribed_at', models.DateTimeField(blank=True, null=True, verbose_name='Транскрибировано')),
                ('error_message', models.TextField(blank=True, verbose_name='Сообщение об ошибке')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
            ],
            options={
                'verbose_name': 'Запись',
                'verbose_name_plural': 'Записи',
                'ordering': ['-created_at'],
            },
        ),
    ]
