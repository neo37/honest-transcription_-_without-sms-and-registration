from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0037_meetingroom_join_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteuser',
            name='google_calendar_token',
            field=models.JSONField(blank=True, null=True, verbose_name='Google Calendar токен'),
        ),
        migrations.AddField(
            model_name='meetingroom',
            name='google_event_id',
            field=models.CharField(blank=True, db_index=True, max_length=200, null=True, verbose_name='Google Event ID'),
        ),
    ]
