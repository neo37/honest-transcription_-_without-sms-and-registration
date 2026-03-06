from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Register Telegram webhook URL with Telegram Bot API'

    def handle(self, *args, **options):
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        if not token:
            self.stdout.write('TELEGRAM_BOT_TOKEN not set, skipping')
            return

        site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
        if not site_url:
            self.stdout.write('SITE_URL not set, skipping webhook registration')
            return

        secret = settings.TELEGRAM_WEBHOOK_SECRET
        webhook_url = f'{site_url}/tg-webhook/{secret}/'

        from recordings.telegram_service import register_webhook
        result = register_webhook(webhook_url)
        if result:
            self.stdout.write(self.style.SUCCESS(f'Telegram webhook registered: {webhook_url}'))
        else:
            self.stdout.write(self.style.WARNING('Failed to register Telegram webhook (check token/SITE_URL)'))
