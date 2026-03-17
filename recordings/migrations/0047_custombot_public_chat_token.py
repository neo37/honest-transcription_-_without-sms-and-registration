import uuid
from django.db import migrations, models


def generate_tokens(apps, schema_editor):
    CustomBot = apps.get_model('recordings', 'CustomBot')
    for bot in CustomBot.objects.all():
        bot.public_chat_token = uuid.uuid4()
        bot.save(update_fields=['public_chat_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0046_recording_transcription_log'),
    ]

    operations = [
        # Step 1: add nullable field (avoids unique collision on existing rows)
        migrations.AddField(
            model_name='custombot',
            name='public_chat_token',
            field=models.UUIDField(
                verbose_name='Токен публичного чата',
                null=True, blank=True, default=None,
            ),
        ),
        # Step 2: fill unique values for all existing rows
        migrations.RunPython(generate_tokens, migrations.RunPython.noop),
        # Step 3: make non-null + unique
        migrations.AlterField(
            model_name='custombot',
            name='public_chat_token',
            field=models.UUIDField(
                verbose_name='Токен публичного чата',
                default=uuid.uuid4, unique=True, editable=False,
            ),
        ),
    ]
