from django.core.management.base import BaseCommand
from django.conf import settings as django_settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from datetime import timedelta
import os
import random
from accounts.models import PasscodeConfig


class Command(BaseCommand):
    help = 'Initialize or reset the passcode configuration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--passcode',
            type=str,
            help='Initial passcode (6 digits). If not provided, uses INITIAL_PASSCODE env or generates random.',
        )

    def handle(self, *args, **options):
        # Get passcode from argument, env, or generate random
        passcode = options.get('passcode')
        if not passcode:
            passcode = os.getenv('INITIAL_PASSCODE')
        if not passcode:
            # Generate random 6-digit passcode
            passcode = str(random.randint(100000, 999999))
            self.stdout.write(
                self.style.WARNING(f'No passcode provided. Generated random passcode: {passcode}')
            )
        
        # Validate passcode
        if len(passcode) != 6 or not passcode.isdigit():
            self.stdout.write(
                self.style.ERROR('Passcode must be exactly 6 digits')
            )
            return
        
        expiry_days = getattr(django_settings, 'PASSCODE_EXPIRY_DAYS', 7)
        # Create or update config
        config, created = PasscodeConfig.objects.get_or_create(
            pk=1,
            defaults={
                'passcode_hash': make_password(passcode),
                'passcode_configured': True,
                'expires_at': timezone.now() + timedelta(days=expiry_days)
            }
        )

        if not created:
            # Update existing
            config.passcode_hash = make_password(passcode)
            config.passcode_configured = True
            config.expires_at = timezone.now() + timedelta(days=expiry_days)
            config.reset_attempts()
            config.save()
            self.stdout.write(
                self.style.SUCCESS(f'Passcode updated successfully')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Passcode initialized successfully')
            )
        
        self.stdout.write(
            self.style.SUCCESS(f'Passcode expires in {expiry_days} days: {config.expires_at.strftime("%Y-%m-%d %H:%M:%S")}')
        )

