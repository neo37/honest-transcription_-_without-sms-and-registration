from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0034_dayshareling'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteuser',
            name='display_name',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Отображаемое имя'),
        ),
        migrations.AddField(
            model_name='siteuser',
            name='avatar_url',
            field=models.CharField(blank=True, default='', max_length=500, verbose_name='Аватар URL'),
        ),
        migrations.AddField(
            model_name='meetingroom',
            name='scheduled_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Запланировано'),
        ),
        migrations.CreateModel(
            name='MeetingAttendee',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('notify_before_minutes', models.IntegerField(default=15, verbose_name='Уведомить за (мин)')),
                ('repeat_every_minutes', models.IntegerField(default=1, verbose_name='Повторять каждые (мин)')),
                ('confirmed_at', models.DateTimeField(blank=True, null=True, verbose_name='Подтверждено')),
                ('last_notified_at', models.DateTimeField(blank=True, null=True, verbose_name='Последнее уведомление')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('meeting', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attendees', to='recordings.meetingroom')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='meeting_attendances', to='recordings.siteuser')),
            ],
            options={
                'verbose_name': 'Участник встречи',
                'verbose_name_plural': 'Участники встреч',
                'unique_together': {('user', 'meeting')},
            },
        ),
    ]
