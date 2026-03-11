from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0023_systemconfig'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteuser',
            name='tg_chat_id',
            field=models.BigIntegerField(blank=True, null=True, verbose_name='Telegram Chat ID'),
        ),
    ]
