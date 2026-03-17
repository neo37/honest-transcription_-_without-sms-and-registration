from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0038_google_calendar'),
    ]

    operations = [
        migrations.AddField(
            model_name='meetingroom',
            name='guest_tg_chat_id',
            field=models.BigIntegerField(blank=True, null=True, verbose_name='Telegram гостя'),
        ),
    ]
