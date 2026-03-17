from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0043_custombot_reply_mode_botcontact'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteuser',
            name='timezone',
            field=models.CharField(default='Europe/Moscow', max_length=60, verbose_name='Часовой пояс'),
        ),
    ]
