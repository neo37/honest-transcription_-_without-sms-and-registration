from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0022_space_wiki_credentials'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=100, unique=True, verbose_name='Ключ')),
                ('value', models.CharField(blank=True, default='', max_length=500, verbose_name='Значение')),
                ('description', models.CharField(blank=True, default='', max_length=300, verbose_name='Описание')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
            ],
            options={
                'verbose_name': 'Системная настройка',
                'verbose_name_plural': 'Системные настройки',
            },
        ),
    ]
