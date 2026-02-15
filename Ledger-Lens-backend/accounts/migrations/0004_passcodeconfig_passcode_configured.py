# Generated migration for passcode_configured (removes reliance on 000000 as "default")

from django.db import migrations, models
from django.contrib.auth.hashers import check_password


def backfill_passcode_configured(apps, schema_editor):
    """Existing rows: if current hash is 000000, leave configured=False; else True."""
    PasscodeConfig = apps.get_model('accounts', 'PasscodeConfig')
    for config in PasscodeConfig.objects.all():
        config.passcode_configured = not check_password('000000', config.passcode_hash)
        config.save()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_passcodeconfig'),
    ]

    operations = [
        migrations.AddField(
            model_name='passcodeconfig',
            name='passcode_configured',
            field=models.BooleanField(
                default=False,
                help_text='True once a passcode has been set (setup page hidden). Allows 000000 as a valid user passcode.'
            ),
        ),
        migrations.RunPython(backfill_passcode_configured, noop_reverse),
    ]
