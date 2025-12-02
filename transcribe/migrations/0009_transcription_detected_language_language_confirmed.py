# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('transcribe', '0008_transcription_user_uuid_uuiduploadcount'),
    ]

    operations = [
        migrations.AddField(
            model_name='transcription',
            name='detected_language',
            field=models.CharField(blank=True, max_length=10, null=True, verbose_name='Определенный язык'),
        ),
        migrations.AddField(
            model_name='transcription',
            name='language_confirmed',
            field=models.BooleanField(default=False, verbose_name='Язык подтвержден пользователем'),
        ),
    ]

