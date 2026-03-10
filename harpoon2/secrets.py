from .settings import BASE_DIR
import os

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

SECRET_KEY = 'django-insecure-dev-key-change-in-production-123456789'

ALLOWED_HOSTS = ['localhost', '127.0.0.1', '192.168.3.27']
