import logging
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        from django.conf import settings
        logger = logging.getLogger(__name__)
        if getattr(settings, 'USE_SUPABASE_STORAGE', False):
            logger.info(
                "Storage: Supabase enabled; bucket: %s, endpoint: %s",
                getattr(settings, 'AWS_STORAGE_BUCKET_NAME', ''),
                getattr(settings, 'AWS_S3_ENDPOINT_URL', ''),
            )
        else:
            logger.info(
                "Storage: local filesystem; MEDIA_ROOT: %s",
                getattr(settings, 'MEDIA_ROOT', ''),
            )
