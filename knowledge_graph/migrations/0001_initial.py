from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('recordings', '0023_systemconfig'),
        ('wiki_kb', '0003_wikiarticle_share_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='KGNode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('node_type', models.CharField(choices=[('person', 'Человек'), ('topic', 'Тема'), ('org', 'Организация'), ('decision', 'Решение'), ('task', 'Задача'), ('recording', 'Запись'), ('wiki', 'Статья вики')], max_length=20)),
                ('title', models.CharField(max_length=300)),
                ('description', models.TextField(blank=True, default='')),
                ('weight', models.FloatField(default=1.0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('space', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='kg_nodes', to='recordings.space')),
                ('recording', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='kg_nodes', to='recordings.recording')),
                ('wiki_article', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='kg_nodes', to='wiki_kb.wikiarticle')),
            ],
            options={'verbose_name': 'Узел графа', 'verbose_name_plural': 'Узлы графа', 'ordering': ['-weight', 'title']},
        ),
        migrations.CreateModel(
            name='KGEdge',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('relation_type', models.CharField(choices=[('mentioned_in', 'Упомянут в'), ('speaks_about', 'Говорит о'), ('decides', 'Принимает решение'), ('assigns_task', 'Назначает задачу'), ('related_to', 'Связан с'), ('part_of', 'Часть')], default='related_to', max_length=30)),
                ('weight', models.FloatField(default=1.0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='outgoing_edges', to='knowledge_graph.kgnode')),
                ('target', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='incoming_edges', to='knowledge_graph.kgnode')),
                ('recording', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='kg_edges', to='recordings.recording')),
                ('wiki_article', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='kg_edges', to='wiki_kb.wikiarticle')),
            ],
            options={'verbose_name': 'Связь графа', 'verbose_name_plural': 'Связи графа'},
        ),
        migrations.AddConstraint(
            model_name='kgnode',
            constraint=models.UniqueConstraint(fields=['space', 'node_type', 'title'], name='unique_kg_node'),
        ),
        migrations.AddConstraint(
            model_name='kgedge',
            constraint=models.UniqueConstraint(fields=['source', 'target', 'relation_type', 'recording', 'wiki_article'], name='unique_kg_edge'),
        ),
    ]
