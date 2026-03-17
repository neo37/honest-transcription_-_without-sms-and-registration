import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0051_siteuser_tg_username_accesslog_tg_username'),
    ]

    operations = [
        migrations.AddField(
            model_name='recording',
            name='owner',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='owned_recordings',
                to='recordings.siteuser',
                verbose_name='Владелец',
            ),
        ),
        migrations.AddField(
            model_name='recording',
            name='is_personal',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Личная запись'),
        ),
    ]
