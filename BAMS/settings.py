from pathlib import Path
import os
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in {'1', 'true', 'yes', 'on'}


def env_int(name, default):
    return int(os.environ.get(name, str(default)))


def env_list(name, default=None):
    value = os.environ.get(name)
    if not value:
        return default or []
    return [item.strip() for item in value.split(',') if item.strip()]

# ── Security ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get('SECRET_KEY')
DEBUG = env_bool('DEBUG', False)
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', [
    'api.bmsapp.com.pk',
    '209.126.5.119',
    '127.0.0.1',
    '192.168.50.15',
])
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', ['https://api.bmsapp.com.pk'])

# ── Apps ──────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third party
    'rest_framework',
    'corsheaders',
    # Our app
    'attendance',
    'django_extensions',
]

# ── Middleware ────────────────────────────────────────────────────────────────
# NOTE: CorsMiddleware must be FIRST, and every entry needs a trailing comma
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',          # ← must be first
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',      # ← fixed missing comma
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'BAMS.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'BAMS.wsgi.application'

# ── Database — MySQL ──────────────────────────────────────────────────────────
# pip install mysqlclient
#
# LOCAL:
# DATABASES = {
#     'default': {
#         'ENGINE':   'django.db.backends.mysql',
#         'NAME':     'bmsappcom',           # your database name
#         'USER':     'root',           # your mysql username
#         'PASSWORD': 'xxxxx',          # your mysql password
#         'HOST':     '127.0.0.1',
#         'PORT':     '3306',
#         'OPTIONS': {
#             'charset': 'utf8mb4',     # full unicode support (emojis etc.)
#         },
#     }
# }


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_NAME'),
        'USER': os.environ.get('DB_USER'),
        'PASSWORD': os.environ.get('DB_PASSWORD'),
        'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
    }
}

# PRODUCTION — just change the four values below, everything else stays the same:
# DATABASES = {
#     'default': {
#         'ENGINE':   'django.db.backends.mysql',
#         'NAME':     os.environ.get('DB_NAME', 'bams'),
#         'USER':     os.environ.get('DB_USER', 'root'),
#         'PASSWORD': os.environ.get('DB_PASSWORD', ''),
#         'HOST':     os.environ.get('DB_HOST', 'localhost'),
#         'PORT':     os.environ.get('DB_PORT', '3306'),
#         'OPTIONS': {
#             'charset': 'utf8mb4',
#         },
#     }
# }

# ── Password Validation ───────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── Localisation ──────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'Asia/Karachi'
USE_I18N      = True
USE_TZ        = True

# ── Static & Media ────────────────────────────────────────────────────────────
STATIC_URL  = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL  = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Upload limits for face enrollment/re-enrollment. Modern phone images can be
# large, so Django must accept a full multi-photo request before face validation
# and resizing rules decide whether each image is usable.
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int('DATA_UPLOAD_MAX_MEMORY_SIZE', 40 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int('FILE_UPLOAD_MAX_MEMORY_SIZE', 12 * 1024 * 1024)

# ── CORS — allow Flutter on any device ───────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = env_bool('CORS_ALLOW_ALL_ORIGINS', DEBUG)
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS')

SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', not DEBUG)
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000' if not DEBUG else '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', not DEBUG)
SECURE_HSTS_PRELOAD = env_bool('SECURE_HSTS_PRELOAD', False)
SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = env_bool('CSRF_COOKIE_SECURE', not DEBUG)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ── Django REST Framework ─────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.environ.get('DRF_ANON_THROTTLE', '30/min'),
        'user': os.environ.get('DRF_USER_THROTTLE', '120/min'),
        'attendance_mark': os.environ.get('DRF_ATTENDANCE_THROTTLE', '10/min'),
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}

# ── Auto Field ───────────────────────────────────────────────────────────────
# Suppresses Django's system check warning about default primary key type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
