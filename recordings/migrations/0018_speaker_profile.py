from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0017_recording_transcription_progress'),
    ]

    operations = [
        migrations.AddField(
            model_name='recording',
            name='speaker_embeddings',
            field=models.JSONField(blank=True, default=dict, verbose_name='Эмбеддинги спикеров'),
        ),
        migrations.CreateModel(
            name='SpeakerProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='Имя')),
                ('embedding', models.JSONField(blank=True, default=list, verbose_name='Эмбеддинг голоса')),
                ('sample_count', models.PositiveIntegerField(default=1, verbose_name='Кол-во образцов')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
                ('space', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='speaker_profiles',
                    to='recordings.space',
                    verbose_name='Пространство',
                )),
            ],
            options={
                'verbose_name': 'Голосовой профиль',
                'verbose_name_plural': 'Голосовые профили',
                'unique_together': {('space', 'name')},
            },
        ),
    ]
