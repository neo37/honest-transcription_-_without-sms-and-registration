# RecordingEmbedding for pgvector (PostgreSQL only)

from django.db import migrations, models
import django.db.models.deletion


def forward_pgvector(apps, schema_editor):
    from django.db import connection
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        c.execute("""
            CREATE TABLE IF NOT EXISTS recordings_recordingembedding (
                recording_id integer NOT NULL PRIMARY KEY REFERENCES recordings_recording(id) ON DELETE CASCADE,
                embedding vector(384)
            );
        """)


def reverse_pgvector(apps, schema_editor):
    from django.db import connection
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as c:
        c.execute("DROP TABLE IF EXISTS recordings_recordingembedding;")


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0004_transcribe_embedding_queues'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='RecordingEmbedding',
                    fields=[
                        ('recording', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='embedding', serialize=False, to='recordings.recording')),
                        ('embedding', models.BinaryField(blank=True, null=True)),
                    ],
                    options={
                        'verbose_name': 'Эмбеддинг записи',
                        'verbose_name_plural': 'Эмбеддинги записей',
                    },
                ),
            ],
            database_operations=[
                migrations.RunPython(forward_pgvector, reverse_pgvector),
            ],
        ),
    ]
