from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0018_speaker_profile'),
    ]

    operations = [
        migrations.AddField(
            model_name='speakerprofile',
            name='speech_patterns',
            field=models.JSONField(
                blank=True,
                default=dict,
                verbose_name='Речевые паттерны',
                help_text='{"top_words": {"слово": freq, ...}, "phrases": {"фраза": freq, ...}, "sample_words": N}',
            ),
        ),
    ]
