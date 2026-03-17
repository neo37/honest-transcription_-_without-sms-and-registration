from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0056_siteuser_last_seen'),
    ]

    operations = [
        migrations.CreateModel(
            name='BPChatTopic',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('thread_id', models.IntegerField(unique=True, verbose_name='Thread ID')),
                ('name', models.CharField(max_length=200, verbose_name='Название')),
                ('icon_color', models.IntegerField(default=0, verbose_name='Цвет иконки')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('last_message_at', models.DateTimeField(blank=True, null=True, verbose_name='Последнее сообщение')),
            ],
            options={'verbose_name': 'Топик БП', 'verbose_name_plural': 'Топики БП', 'ordering': ['-last_message_at']},
        ),
        migrations.CreateModel(
            name='BPChatMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('tg_message_id', models.BigIntegerField(unique=True, verbose_name='TG message_id')),
                ('from_name', models.CharField(max_length=200, verbose_name='Имя отправителя')),
                ('from_tg_id', models.BigIntegerField(blank=True, null=True, verbose_name='TG user_id')),
                ('from_username', models.CharField(blank=True, max_length=100, verbose_name='TG username')),
                ('text', models.TextField(verbose_name='Текст')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Отправлено')),
                ('tg_date', models.DateTimeField(blank=True, null=True, verbose_name='Дата TG')),
                ('via_site', models.BooleanField(default=False, verbose_name='Отправлено с сайта')),
                ('topic', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bp_messages', to='recordings.bpchattopic')),
                ('from_site_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bp_messages', to='recordings.siteuser')),
            ],
            options={'verbose_name': 'Сообщение БП', 'verbose_name_plural': 'Сообщения БП', 'ordering': ['tg_date', 'created_at']},
        ),
    ]
