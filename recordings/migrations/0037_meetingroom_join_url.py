from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0036_dayshareling_permanent'),
    ]

    operations = [
        migrations.AddField(
            model_name='meetingroom',
            name='join_url',
            field=models.URLField('Ссылка на встречу', max_length=500, null=True, blank=True),
        ),
    ]
