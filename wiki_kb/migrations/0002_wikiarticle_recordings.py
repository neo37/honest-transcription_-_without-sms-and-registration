from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wiki_kb', '0001_initial'),
        ('recordings', '0010_space_apikey_ocr_space'),
    ]

    operations = [
        migrations.AddField(
            model_name='wikiarticle',
            name='recordings',
            field=models.ManyToManyField(
                blank=True,
                related_name='wiki_articles',
                to='recordings.recording',
                verbose_name='Связанные записи',
            ),
        ),
    ]
