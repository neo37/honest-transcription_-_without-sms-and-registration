# Generated migration for OcrJob (olmOCR)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0005_recording_embedding_pgvector'),
    ]

    operations = [
        migrations.CreateModel(
            name='OcrJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('original_filename', models.CharField(max_length=255, verbose_name='Имя файла')),
                ('file_path', models.CharField(blank=True, max_length=512, verbose_name='Путь к файлу')),
                ('status', models.CharField(choices=[('pending', 'В очереди'), ('processing', 'Обрабатывается'), ('done', 'Готово'), ('failed', 'Ошибка')], db_index=True, default='pending', max_length=20, verbose_name='Статус')),
                ('result_markdown', models.TextField(blank=True, verbose_name='Результат (Markdown)')),
                ('error_message', models.TextField(blank=True, verbose_name='Ошибка')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
            ],
            options={
                'verbose_name': 'Задача OCR',
                'verbose_name_plural': 'Задачи OCR',
                'ordering': ['-created_at'],
            },
        ),
    ]
