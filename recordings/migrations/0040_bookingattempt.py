from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('recordings', '0039_meetingroom_guest_tg_chat_id'),
    ]
    operations = [
        migrations.CreateModel(
            name='BookingAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.GenericIPAddressField(verbose_name='IP')),
                ('fingerprint', models.CharField(blank=True, max_length=64, verbose_name='Fingerprint')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={'verbose_name': 'Попытка бронирования'},
        ),
    ]
