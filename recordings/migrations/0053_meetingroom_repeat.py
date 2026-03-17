from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0052_recording_owner_personal'),
    ]

    operations = [
        migrations.AddField(
            model_name='meetingroom',
            name='repeat',
            field=models.CharField(
                blank=True,
                choices=[('', 'Не повторяется'), ('weekly', 'Еженедельно')],
                default='',
                max_length=20,
                verbose_name='Повтор',
            ),
        ),
    ]
