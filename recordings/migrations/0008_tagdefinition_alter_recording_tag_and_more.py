import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0007_recording_quality_language'),
    ]

    operations = [
        migrations.CreateModel(
            name='TagDefinition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=50, unique=True, verbose_name='Ключ (slug)')),
                ('label', models.CharField(max_length=100, verbose_name='Название')),
                ('filename_pattern', models.CharField(
                    blank=True,
                    help_text='Если задана, записи с этой подстрокой в имени будут авто-тегироваться (без учёта регистра)',
                    max_length=200,
                    verbose_name='Подстрока в имени файла',
                )),
                ('order', models.PositiveIntegerField(default=0, verbose_name='Порядок отображения')),
            ],
            options={
                'verbose_name': 'Тег (фильтр)',
                'verbose_name_plural': 'Теги (фильтры)',
                'ordering': ['order', 'label'],
            },
        ),
        migrations.AlterField(
            model_name='recording',
            name='tag',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', '—'), ('analytics', 'Аналитика'), ('backend', 'Бекенд'),
                    ('infra', 'Инфра'), ('daily', 'Дейли'), ('marketing', 'Маркетинг'),
                    ('frontend', 'Фронт'),
                ],
                db_index=True,
                max_length=20,
                verbose_name='Тег',
            ),
        ),
        migrations.CreateModel(
            name='AccessLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('username', models.CharField(blank=True, max_length=150, verbose_name='Пользователь')),
                ('ip', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP адрес')),
                ('user_agent', models.TextField(blank=True, verbose_name='User-Agent')),
                ('os_name', models.CharField(blank=True, max_length=200, verbose_name='ОС')),
                ('screen', models.CharField(blank=True, max_length=30, verbose_name='Разрешение экрана')),
                ('event', models.CharField(
                    choices=[('login', 'Вход'), ('view', 'Просмотр записи')],
                    db_index=True,
                    max_length=20,
                    verbose_name='Событие',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Время')),
                ('recording', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='access_logs',
                    to='recordings.recording',
                    verbose_name='Запись',
                )),
            ],
            options={
                'verbose_name': 'Лог доступа',
                'verbose_name_plural': 'Логи доступа',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ShareToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='Токен')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активна')),
                ('recording', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='share_tokens',
                    to='recordings.recording',
                    verbose_name='Запись',
                )),
            ],
            options={
                'verbose_name': 'Публичная ссылка',
                'verbose_name_plural': 'Публичные ссылки',
                'ordering': ['-created_at'],
            },
        ),
    ]
