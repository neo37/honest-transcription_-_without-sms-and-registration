from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0035_profile_attendee'),
    ]

    operations = [
        migrations.AddField(
            model_name='daysharelink',
            name='is_permanent',
            field=models.BooleanField(default=False, verbose_name='Постоянная (всегда завтра)'),
        ),
        migrations.AlterField(
            model_name='daysharelink',
            name='date',
            field=models.DateField(blank=True, null=True, verbose_name='Дата'),
        ),
    ]
