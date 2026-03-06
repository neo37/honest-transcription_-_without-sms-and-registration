from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0013_auto_sync'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteuser',
            name='tg_verify_code',
            field=models.CharField(blank=True, max_length=16, null=True, verbose_name='Код TG верификации'),
        ),
        migrations.AddField(
            model_name='siteuser',
            name='tg_verify_expires',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Срок действия кода TG'),
        ),
        migrations.AddField(
            model_name='siteuser',
            name='tg_verified',
            field=models.BooleanField(default=False, verbose_name='TG верифицирован'),
        ),
    ]
