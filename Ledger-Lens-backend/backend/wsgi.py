"""
WSGI config for backend project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

application = get_wsgi_application()

# Resume incomplete PDFs after Django is loaded
try:
    from backend import startup_resume
    startup_resume()
except Exception as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.error(f"[STARTUP] Failed to resume incomplete PDFs: {str(e)}")