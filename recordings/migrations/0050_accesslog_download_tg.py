from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0049_micropreset'),
    ]

    operations = [
        migrations.AddField(
            model_name='accesslog',
            name='tg_chat_id',
            field=models.BigIntegerField(blank=True, null=True, verbose_name='Telegram Chat ID'),
        ),
        migrations.AlterField(
            model_name='accesslog',
            name='event',
            field=models.CharField(
                choices=[('login', 'Вход'), ('view', 'Просмотр записи'), ('download', 'Скачивание записи')],
                db_index=True,
                max_length=20,
                verbose_name='Событие',
            ),
        ),
    ]
