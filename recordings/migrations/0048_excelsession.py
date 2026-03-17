import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0047_custombot_public_chat_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExcelSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('filename', models.CharField(default='export', max_length=256)),
                ('columns', models.JSONField(default=list)),
                ('rows', models.JSONField(default=list)),
                ('col_configs', models.JSONField(default=dict)),
                ('tg_chat_id', models.BigIntegerField(blank=True, null=True)),
                ('created_via', models.CharField(default='web', max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                           related_name='excel_sessions', to='recordings.siteuser')),
                ('space', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                            related_name='excel_sessions', to='recordings.space')),
            ],
            options={'verbose_name': 'Excel-сессия', 'verbose_name_plural': 'Excel-сессии', 'ordering': ['-created_at']},
        ),
    ]
