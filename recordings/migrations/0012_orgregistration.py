from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0010_space_apikey_ocr_space'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrgRegistration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('org_name', models.CharField(max_length=200, verbose_name='Название организации')),
                ('email', models.EmailField(verbose_name='Email администратора')),
                ('verify_code', models.CharField(max_length=16, unique=True, verbose_name='Код подтверждения')),
                ('status', models.CharField(
                    choices=[('pending', 'Ожидает подтверждения'), ('verified', 'Подтверждено')],
                    default='pending',
                    max_length=20,
                    verbose_name='Статус',
                )),
                ('tg_chat_id', models.BigIntegerField(blank=True, null=True, verbose_name='Telegram chat ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('space', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='org_registrations',
                    to='recordings.space',
                    verbose_name='Пространство',
                )),
            ],
            options={
                'verbose_name': 'Регистрация организации',
                'verbose_name_plural': 'Регистрации организаций',
                'ordering': ['-created_at'],
            },
        ),
    ]
