from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0020_mascotlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='MascotTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('room', models.CharField(max_length=200, verbose_name='Комната')),
                ('title', models.CharField(max_length=500, verbose_name='Задача')),
                ('speaker', models.CharField(blank=True, max_length=200, verbose_name='Автор')),
                ('done', models.BooleanField(default=False, verbose_name='Выполнена')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Время')),
            ],
            options={
                'verbose_name': 'Задача Маскота',
                'verbose_name_plural': 'Задачи Маскота',
                'ordering': ['-created_at'],
            },
        ),
    ]
