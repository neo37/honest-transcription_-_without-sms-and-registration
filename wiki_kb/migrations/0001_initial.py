from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('recordings', '0010_space_apikey_ocr_space'),
    ]

    operations = [
        migrations.CreateModel(
            name='WikiArticle',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=256, verbose_name='Заголовок')),
                ('slug', models.SlugField(max_length=256, unique=True, verbose_name='Slug')),
                ('content', models.TextField(blank=True, default='', verbose_name='Содержимое (Markdown)')),
                ('order', models.IntegerField(default=0, verbose_name='Порядок')),
                ('is_deleted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('parent', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='children',
                    to='wiki_kb.wikiarticle',
                    verbose_name='Родительская статья',
                )),
                ('space', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='wiki_articles',
                    to='recordings.space',
                    verbose_name='Пространство',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_wiki_articles',
                    to='recordings.siteuser',
                    verbose_name='Создал',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='updated_wiki_articles',
                    to='recordings.siteuser',
                    verbose_name='Изменил',
                )),
            ],
            options={
                'verbose_name': 'Статья',
                'verbose_name_plural': 'Статьи',
                'ordering': ['order', 'title'],
            },
        ),
        migrations.CreateModel(
            name='WikiRevision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content', models.TextField(verbose_name='Содержимое')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Время правки')),
                ('comment', models.CharField(blank=True, default='', max_length=256, verbose_name='Комментарий')),
                ('article', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='revisions',
                    to='wiki_kb.wikiarticle',
                    verbose_name='Статья',
                )),
                ('revised_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to='recordings.siteuser',
                    verbose_name='Автор правки',
                )),
            ],
            options={
                'verbose_name': 'Ревизия',
                'verbose_name_plural': 'Ревизии',
                'ordering': ['-created_at'],
            },
        ),
    ]
