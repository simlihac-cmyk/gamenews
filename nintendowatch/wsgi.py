import os

from django.core.wsgi import get_wsgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nintendowatch.settings")

application = get_wsgi_application()

