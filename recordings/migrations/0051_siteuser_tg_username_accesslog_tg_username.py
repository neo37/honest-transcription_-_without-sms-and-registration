from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0050_accesslog_download_tg'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteuser',
            name='tg_username',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Telegram username'),
        ),
        migrations.AddField(
            model_name='accesslog',
            name='tg_username',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Telegram username'),
        ),
    ]
