from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0041_recurringbusytime'),
    ]

    operations = [
        migrations.AddField(
            model_name='custombot',
            name='system_prompt',
            field=models.TextField(
                blank=True,
                default='',
                verbose_name='Системный промт',
                help_text='Если заполнено — заменяет стандартный промт бота',
            ),
        ),
    ]
