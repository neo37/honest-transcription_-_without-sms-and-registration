from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wiki_kb', '0006_alter_wikiarticleembedding_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='wikiarticle',
            name='is_personal',
            field=models.BooleanField(
                default=False,
                verbose_name='Персональный',
                help_text='Виден только владельцу (created_by) и его кастомным ботам',
            ),
        ),
    ]
