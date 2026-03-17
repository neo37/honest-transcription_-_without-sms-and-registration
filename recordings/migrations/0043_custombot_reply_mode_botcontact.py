from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recordings', '0042_custombot_system_prompt'),
    ]

    operations = [
        migrations.AddField(
            model_name='custombot',
            name='reply_mode',
            field=models.CharField(
                choices=[
                    ('auto', 'Авто — отвечать на все сообщения'),
                    ('after_delay', 'После паузы — если владелец молчит N минут'),
                    ('trigger', 'По слову — только если есть ключевое слово'),
                    ('off', 'Выключен — не отвечать'),
                ],
                default='auto',
                max_length=20,
                verbose_name='Режим ответа',
            ),
        ),
        migrations.AddField(
            model_name='custombot',
            name='reply_delay_m',
            field=models.PositiveIntegerField(
                default=5,
                verbose_name='Пауза (мин)',
                help_text='Для режима «после паузы»: бот отвечает если владелец молчал N минут',
            ),
        ),
        migrations.AddField(
            model_name='custombot',
            name='trigger_word',
            field=models.CharField(
                blank=True,
                default='',
                max_length=100,
                verbose_name='Ключевое слово',
                help_text='Для режима «по слову»: бот отвечает только если сообщение содержит это слово',
            ),
        ),
        migrations.CreateModel(
            name='BotContact',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('tg_user_id', models.BigIntegerField(verbose_name='Telegram user_id')),
                ('first_name', models.CharField(blank=True, default='', max_length=200, verbose_name='Имя')),
                ('last_name', models.CharField(blank=True, default='', max_length=200, verbose_name='Фамилия')),
                ('username', models.CharField(blank=True, default='', max_length=100, verbose_name='Username')),
                ('first_seen', models.DateTimeField(auto_now_add=True, verbose_name='Первый контакт')),
                ('last_seen', models.DateTimeField(auto_now=True, verbose_name='Последний контакт')),
                ('note', models.TextField(blank=True, default='', verbose_name='Заметка',
                                          help_text='Личная заметка владельца об этом контакте')),
                ('bot', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='contacts',
                    to='recordings.custombot',
                    verbose_name='Бот',
                )),
            ],
            options={
                'verbose_name': 'Контакт бота',
                'verbose_name_plural': 'Контакты ботов',
                'ordering': ['-last_seen'],
                'unique_together': {('bot', 'tg_user_id')},
            },
        ),
    ]
