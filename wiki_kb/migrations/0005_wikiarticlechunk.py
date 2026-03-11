from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('wiki_kb', '0004_wikiarticleembedding'),
    ]

    operations = [
        migrations.CreateModel(
            name='WikiArticleChunk',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chunk_index', models.SmallIntegerField(verbose_name='Индекс чанка', default=0)),
                ('chunk_text', models.TextField(verbose_name='Текст чанка')),
                ('embedding', models.JSONField(verbose_name='Вектор')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('article', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='chunks',
                    to='wiki_kb.wikiarticle',
                    verbose_name='Статья',
                )),
            ],
            options={
                'verbose_name': 'Чанк статьи',
                'verbose_name_plural': 'Чанки статей',
                'unique_together': {('article', 'chunk_index')},
            },
        ),
    ]
