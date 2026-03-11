from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0032_alter_botchathistory_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='orgregistration',
            name='target_space',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='target_registrations',
                to='recordings.space',
                verbose_name='Целевое пространство (если задано — не создаётся новое)',
            ),
        ),
    ]
