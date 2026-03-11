from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0019_speakerprofile_speech_patterns'),
    ]

    operations = [
        migrations.CreateModel(
            name='MascotLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('room', models.CharField(max_length=200, verbose_name='Комната')),
                ('event', models.CharField(choices=[
                    ('joined', 'Вошёл в комнату'),
                    ('heard', 'Услышал'),
                    ('said', 'Сказал'),
                    ('wake', 'Wake-word'),
                    ('emoji', 'Emoji реакция'),
                    ('chat', 'Сообщение в чате'),
                ], max_length=20, verbose_name='Событие')),
                ('text', models.TextField(blank=True, verbose_name='Текст')),
                ('speaker', models.CharField(blank=True, max_length=200, verbose_name='Участник')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Время')),
            ],
            options={
                'verbose_name': 'Лог Маскота',
                'verbose_name_plural': 'Логи Маскота',
                'ordering': ['-created_at'],
            },
        ),
    ]
