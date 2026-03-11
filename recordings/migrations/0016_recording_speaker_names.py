from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0015_magiclogintoken'),
    ]

    operations = [
        migrations.AddField(
            model_name='recording',
            name='speaker_names',
            field=models.JSONField(blank=True, default=dict, verbose_name='Имена спикеров'),
        ),
    ]
