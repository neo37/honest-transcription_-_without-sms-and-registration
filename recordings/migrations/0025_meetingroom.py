from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0024_siteuser_tg_chat_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='MeetingRoom',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('room_name', models.CharField(max_length=100, unique=True, verbose_name='ID комнаты')),
                ('title', models.CharField(max_length=200, verbose_name='Название встречи')),
                ('with_mascot', models.BooleanField(default=False, verbose_name='С маскотом')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='meetings',
                    to='recordings.siteuser',
                )),
                ('space', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='meetings',
                    to='recordings.space',
                )),
            ],
            options={
                'verbose_name': 'Встреча',
                'verbose_name_plural': 'Встречи',
                'ordering': ['-created_at'],
            },
        ),
    ]
