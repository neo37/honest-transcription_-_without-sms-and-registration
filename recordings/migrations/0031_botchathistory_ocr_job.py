from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0030_botchathistory_recording'),
    ]

    operations = [
        migrations.AddField(
            model_name='botchathistory',
            name='ocr_job',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='bot_history_entries',
                to='recordings.ocrjob',
                verbose_name='OCR задача',
            ),
        ),
    ]
