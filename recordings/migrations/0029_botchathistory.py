from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0028_custombot_botsetupstate'),
    ]

    operations = [
        migrations.CreateModel(
            name='BotChatHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('chat_id', models.BigIntegerField(verbose_name='Telegram chat_id')),
                ('bot_id', models.IntegerField(null=True, blank=True, verbose_name='CustomBot pk (null=главный)')),
                ('role', models.CharField(max_length=16, verbose_name='Роль')),  # user / assistant / tool
                ('content', models.TextField(verbose_name='Содержимое')),
                ('tool_name', models.CharField(max_length=64, blank=True, default='', verbose_name='Инструмент')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'История чата бота',
                'verbose_name_plural': 'История чатов ботов',
                'ordering': ['created_at'],
                'indexes': [
                    models.Index(fields=['chat_id', 'bot_id', 'created_at'], name='bot_chat_history_idx'),
                ],
            },
        ),
    ]
