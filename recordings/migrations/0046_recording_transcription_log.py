from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0045_alter_botcontact_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='recording',
            name='transcription_log',
            field=models.JSONField(
                blank=True,
                default=list,
                verbose_name='Лог транскрибации',
            ),
        ),
    ]
