from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0027_alter_recording_transcription_stage'),
        ('wiki_kb', '0004_wikiarticleembedding'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomBot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(max_length=200, unique=True, verbose_name='Токен бота')),
                ('name', models.CharField(blank=True, default='', max_length=200, verbose_name='Название бота')),
                ('username', models.CharField(blank=True, default='', max_length=100, verbose_name='Username бота')),
                ('webhook_secret', models.CharField(blank=True, default='', max_length=64, verbose_name='Webhook secret')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='custom_bots', to='recordings.siteuser', verbose_name='Владелец')),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='custom_bots', to='recordings.space', verbose_name='Пространство')),
                ('root_article', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='custom_bots', to='wiki_kb.wikiarticle', verbose_name='Раздел вики')),
            ],
            options={'verbose_name': 'Кастомный бот', 'verbose_name_plural': 'Кастомные боты'},
        ),
        migrations.CreateModel(
            name='BotSetupState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chat_id', models.BigIntegerField(unique=True, verbose_name='Telegram chat_id')),
                ('state', models.CharField(blank=True, default='', max_length=50, verbose_name='Состояние')),
                ('pending_token', models.CharField(blank=True, default='', max_length=200, verbose_name='Pending token')),
                ('pending_bot_pk', models.IntegerField(blank=True, null=True, verbose_name='Pending bot pk')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Состояние setup-wizard', 'verbose_name_plural': 'Состояния setup-wizard'},
        ),
    ]
