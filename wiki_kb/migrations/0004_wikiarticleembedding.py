from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('wiki_kb', '0003_wikiarticle_share_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='WikiArticleEmbedding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('embedding', models.JSONField(verbose_name='Вектор')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('article', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='embedding',
                    to='wiki_kb.wikiarticle',
                    verbose_name='Статья',
                )),
            ],
            options={'verbose_name': 'Эмбеддинг статьи', 'verbose_name_plural': 'Эмбеддинги статей'},
        ),
    ]
