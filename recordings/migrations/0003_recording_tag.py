# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0002_add_comment'),
    ]

    operations = [
        migrations.AddField(
            model_name='recording',
            name='tag',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', '—'),
                    ('analytics', 'Аналитика'),
                    ('backend', 'Бекенд'),
                    ('infra', 'Инфра'),
                    ('daily', 'Дейли'),
                    ('marketing', 'Маркетинг'),
                    ('frontend', 'Фронт'),
                ],
                db_index=True,
                default='',
                max_length=20,
                verbose_name='Тег',
            ),
            preserve_default=True,
        ),
    ]
