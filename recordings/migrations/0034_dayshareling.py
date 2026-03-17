import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0033_orgregistration_target_space'),
    ]

    operations = [
        migrations.CreateModel(
            name='DayShareLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(verbose_name='Дата')),
                ('share_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='Токен')),
                ('busy_slots', models.JSONField(default=list, verbose_name='Занятые слоты')),
                ('slot_duration_minutes', models.IntegerField(default=30, verbose_name='Длительность слота (мин)')),
                ('day_start', models.CharField(default='09:00', max_length=5, verbose_name='Начало дня')),
                ('day_end', models.CharField(default='18:00', max_length=5, verbose_name='Конец дня')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='day_shares',
                    to='recordings.siteuser',
                    verbose_name='Владелец',
                )),
            ],
            options={
                'verbose_name': 'Ссылка-поделиться днём',
                'verbose_name_plural': 'Ссылки-поделиться днём',
            },
        ),
    ]
