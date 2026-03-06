# Manual migration: queues for transcription and embedding

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0003_recording_tag'),
    ]

    operations = [
        migrations.CreateModel(
            name='TranscribeQueue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('priority', models.PositiveSmallIntegerField(default=0, help_text='1 = по кнопке (вне очереди), 0 = с поллера', verbose_name='Приоритет')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Добавлено')),
                ('recording', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='transcribe_queue_entry', to='recordings.recording')),
            ],
            options={
                'verbose_name': 'Задача транскрибации',
                'verbose_name_plural': 'Очередь транскрибации',
                'ordering': ['-priority', 'created_at'],
            },
        ),
        migrations.CreateModel(
            name='EmbeddingQueue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Добавлено')),
                ('recording', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='embedding_queue_entry', to='recordings.recording')),
            ],
            options={
                'verbose_name': 'Задача эмбеддинга',
                'verbose_name_plural': 'Очередь эмбеддингов',
                'ordering': ['created_at'],
            },
        ),
    ]
