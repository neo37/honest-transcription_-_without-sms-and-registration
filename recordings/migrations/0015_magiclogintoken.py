import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0014_siteuser_tg_verify'),
    ]

    operations = [
        migrations.CreateModel(
            name='MagicLoginToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='Токен')),
                ('expires_at', models.DateTimeField(verbose_name='Действует до')),
                ('used_at', models.DateTimeField(blank=True, null=True, verbose_name='Использован')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='magic_tokens',
                    to='recordings.siteuser',
                    verbose_name='Пользователь',
                )),
            ],
            options={
                'verbose_name': 'Magic-ссылка',
                'verbose_name_plural': 'Magic-ссылки',
            },
        ),
    ]
