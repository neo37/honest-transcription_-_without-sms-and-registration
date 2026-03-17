from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0040_bookingattempt'),
    ]

    operations = [
        migrations.CreateModel(
            name='RecurringBusyTime',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=100, verbose_name='Название')),
                ('start_time', models.TimeField(verbose_name='Начало')),
                ('end_time', models.TimeField(verbose_name='Конец')),
                ('repeat', models.CharField(
                    choices=[('daily', 'Ежедневно'), ('weekdays', 'По будням (пн–пт)')],
                    default='daily', max_length=20, verbose_name='Повтор',
                )),
                ('is_active', models.BooleanField(default=True, verbose_name='Активно')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='recurring_busy_times',
                    to='recordings.siteuser',
                    verbose_name='Владелец',
                )),
            ],
            options={
                'verbose_name': 'Повторяющееся занятое время',
                'verbose_name_plural': 'Повторяющееся занятое время',
                'ordering': ['start_time'],
            },
        ),
    ]
