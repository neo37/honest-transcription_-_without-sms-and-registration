from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0008_tagdefinition_alter_recording_tag_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Space',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='Название')),
                ('slug', models.SlugField(max_length=80, unique=True, verbose_name='Slug')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
            ],
            options={
                'verbose_name': 'Пространство',
                'verbose_name_plural': 'Пространства',
            },
        ),
        migrations.CreateModel(
            name='SiteUser',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, unique=True, verbose_name='Email')),
                ('password', models.CharField(max_length=256, verbose_name='Пароль (хеш)')),
                ('free_left', models.IntegerField(
                    blank=True,
                    default=5,
                    help_text='null = безлимит (BP + участники пространства)',
                    null=True,
                    verbose_name='Осталось бесплатных транскрибаций',
                )),
                ('first_login_at', models.DateTimeField(blank=True, null=True, verbose_name='Первый вход')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('space', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='members',
                    to='recordings.space',
                    verbose_name='Пространство',
                )),
            ],
            options={
                'verbose_name': 'Пользователь сайта',
                'verbose_name_plural': 'Пользователи сайта',
            },
        ),
        migrations.AddField(
            model_name='recording',
            name='space',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='recordings',
                to='recordings.space',
                verbose_name='Пространство',
            ),
        ),
    ]
