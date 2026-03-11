from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0025_meetingroom'),
    ]

    operations = [
        migrations.AddField(
            model_name='meetingroom',
            name='ended_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Завершена'),
        ),
    ]
