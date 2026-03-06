from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wiki_kb', '0002_wikiarticle_recordings'),
    ]

    operations = [
        migrations.AddField(
            model_name='wikiarticle',
            name='share_token',
            field=models.UUIDField(
                verbose_name='Токен публичного доступа',
                null=True, blank=True, unique=True,
            ),
        ),
    ]
