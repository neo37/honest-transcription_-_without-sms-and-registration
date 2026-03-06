from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0006_ocrjob'),
    ]

    operations = [
        migrations.AddField(
            model_name='recording',
            name='transcription_language',
            field=models.CharField(choices=[('ru', 'Русский'), ('en', 'Английский'), ('auto', 'Автоопределение')], default='ru', max_length=10, verbose_name='Язык транскрибации'),
        ),
        migrations.AddField(
            model_name='recording',
            name='transcription_quality',
            field=models.CharField(choices=[('base', 'Базовое (быстро)'), ('small', 'Среднее'), ('medium', 'Хорошее'), ('large-v3', 'Наилучшее (медленно)')], default='base', max_length=20, verbose_name='Качество транскрибации'),
        ),
        migrations.AddField(
            model_name='recording',
            name='ai_title',
            field=models.CharField(blank=True, max_length=255, verbose_name='AI Название'),
        ),
        migrations.AddField(
            model_name='recording',
            name='ai_summary',
            field=models.TextField(blank=True, verbose_name='AI Саммари'),
        ),
    ]
