import uuid
import django.db.models.deletion
from django.db import migrations, models


def populate_space_api_keys(apps, schema_editor):
    Space = apps.get_model('recordings', 'Space')
    for space in Space.objects.all():
        space.api_key = uuid.uuid4()
        space.save(update_fields=['api_key'])


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0009_space_siteuser_recording_space'),
    ]

    operations = [
        # 1. Add api_key as nullable first (no unique)
        migrations.AddField(
            model_name='space',
            name='api_key',
            field=models.UUIDField(blank=True, null=True, verbose_name='API ключ'),
        ),
        # 2. Populate existing rows with unique UUIDs
        migrations.RunPython(populate_space_api_keys, migrations.RunPython.noop),
        # 3. Now make it NOT NULL, unique, with default for new rows
        migrations.AlterField(
            model_name='space',
            name='api_key',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='API ключ'),
        ),
        # 4. OcrJob: add space FK
        migrations.AddField(
            model_name='ocrjob',
            name='space',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='ocr_jobs',
                to='recordings.space',
                verbose_name='Пространство',
            ),
        ),
        # 5. OcrJob: add share_token
        migrations.AddField(
            model_name='ocrjob',
            name='share_token',
            field=models.UUIDField(blank=True, null=True, unique=True, verbose_name='Токен публичного доступа'),
        ),
        # 6. OcrJob: add is_public
        migrations.AddField(
            model_name='ocrjob',
            name='is_public',
            field=models.BooleanField(default=False, verbose_name='Публичный доступ'),
        ),
    ]
