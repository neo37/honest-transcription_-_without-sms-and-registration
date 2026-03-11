from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0029_botchathistory'),
    ]

    operations = [
        migrations.AddField(
            model_name='botchathistory',
            name='recording',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='bot_history_entries',
                to='recordings.recording',
                verbose_name='Запись (аудио/видео)',
            ),
        ),
    ]
