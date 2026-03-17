from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0048_excelsession'),
    ]

    operations = [
        migrations.CreateModel(
            name='MicroPreset',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('wiki_slug', models.CharField(blank=True, max_length=300)),
                ('col_configs', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='micropresets', to='recordings.siteuser')),
            ],
            options={
                'verbose_name': 'Микропресет',
                'verbose_name_plural': 'Микропресеты',
                'ordering': ['-updated_at'],
            },
        ),
    ]
