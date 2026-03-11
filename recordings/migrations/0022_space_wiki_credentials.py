from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0021_mascottask'),
    ]

    operations = [
        migrations.AddField(
            model_name='space',
            name='wiki_username',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Wiki API логин'),
        ),
        migrations.AddField(
            model_name='space',
            name='wiki_password',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Wiki API пароль'),
        ),
    ]
