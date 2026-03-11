from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0016_recording_speaker_names'),
    ]

    operations = [
        migrations.AddField(
            model_name='recording',
            name='transcription_progress',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='Прогресс транскрибации (%)'),
        ),
        migrations.AddField(
            model_name='recording',
            name='transcription_stage',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Этап транскрибации'),
        ),
    ]
