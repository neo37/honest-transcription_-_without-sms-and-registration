from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0055_directmessage'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteuser',
            name='last_seen',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Последняя активность'),
        ),
    ]
