import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG') == 'True'

# ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS').split(',')
ALLOWED_HOSTS = ['*']

WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "") #ai
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1") #ai


# Application definition

INSTALLED_APPS = [
    'unfold',
    'unfold.contrib.filters',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',
    'cloudinary_storage',
    'cloudinary',
    'solo',
    'django_ckeditor_5',
    'core',
    'accounts',
    'wardrobe',
    'news',
    'avatars',
    'wardrobe_items_ai',
    'outfits',
    'model3d',
    'social',
    'feed',
    'django_cleanup.apps.CleanupConfig',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'core.middleware.ApiExceptionMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

AUTH_USER_MODEL = 'accounts.User'

ROOT_URLCONF = 'Khzanti.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
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

ASGI_APPLICATION = 'Khzanti.asgi.application'


# Database

import dj_database_url

DATABASES = {
    "default": dj_database_url.parse(
        os.getenv("DATABASE_URL"),
        conn_max_age=600,
        ssl_require=True,
    )
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    # {
    #     'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    # },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    # {
    #     'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    # },
    # {
    #     'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    # },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
SITE_LOGO_URL = f"/{STATIC_URL}img/Logo.png"
SITE_LOGO_DARK_URL = f"/{STATIC_URL}img/Logo_dark.png"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

CLOUDINARY_STORAGE = {
    "CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME"),
    "API_KEY": os.getenv("CLOUDINARY_API_KEY"),
    "API_SECRET": os.getenv("CLOUDINARY_API_SECRET"),
}

STORAGES = {
    "default": {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

import sys

if "test" in sys.argv:
    try:
        import cloudinary.uploader
        cloudinary.uploader.upload = lambda *args, **kwargs: {
            "public_id": "test_public_id",
            "url": "https://res.cloudinary.com/test_cloud/image/upload/v12345/test.png",
            "secure_url": "https://res.cloudinary.com/test_cloud/image/upload/v12345/test.png",
            "format": "png",
            "resource_type": "image",
        }
    except Exception:
        pass

CORS_ALLOWED_ORIGINS = [
    origin for origin in os.getenv('CORS_ALLOWED_ORIGINS', '').split(',') if origin
]
CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if origin
]

from datetime import timedelta

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=7),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["core.renderers.StandardRenderer"],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "120/min",
        "signup": "10/hour",
        "login": "20/hour",
        "forgot_password": "5/hour",
        "verify_reset_otp": "10/hour",
        "resend_otp": "5/hour",
    },
}


# OTP / password reset

OTP_EXPIRY_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
PASSWORD_RESET_TOKEN_EXPIRY_MINUTES = 10


# Email

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@khzanti.com")
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")


# Cache (used to cache the SiteSettings singleton — cleared automatically on every save)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

SOLO_CACHE = "default"
SOLO_CACHE_TIMEOUT = 60 * 60 * 24  # 1 day; refreshed immediately whenever SiteSettings is saved


CKEDITOR_5_CONFIGS = {
    "default": {
        "toolbar": [
            "heading", "|",
            "bold", "italic", "underline", "link", "|",
            "bulletedList", "numberedList", "blockQuote", "|",
            "undo", "redo", "|",
            "sourceEditing",
        ],
    },
    "blog": {
        "toolbar": [
            "heading", "|",
            "bold", "italic", "underline", "link", "|",
            "bulletedList", "numberedList", "blockQuote", "|",
            "insertImage", "mediaEmbed", "insertTable", "|",
            "undo", "redo", "|",
            "sourceEditing",
        ],
        "image": {
            "toolbar": [
                "imageTextAlternative", "|",
                "imageStyle:alignLeft",
                "imageStyle:alignCenter",
                "imageStyle:alignRight",
                "imageStyle:side",
            ],
            "styles": [
                "full",
                "side",
                "alignLeft",
                "alignCenter",
                "alignRight",
            ],
        },
        "table": {
            "contentToolbar": [
                "tableColumn",
                "tableRow",
                "mergeTableCells",
            ],
        },
    },
}

CKEDITOR_5_FILE_UPLOAD_PERMISSION = "staff"


SPECTACULAR_SETTINGS = {
    "TITLE": "Hdoom-i API",
    "DESCRIPTION": "API documentation for Hdoom-i",
    "VERSION": "1.0.0",

    "CONTACT": {
        "name": "Khirul Islam",
        "email": "khirulislam@proton.me"
    },

    # Separate request and response schemas
    "COMPONENT_SPLIT_REQUEST": True,

    # Keep JWT token after page refresh
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
    },

    # JWT Bearer auth button
    "SECURITY": [{"BearerAuth": []}],
    "PATH_PREFIX": "/api/v1",

    "ENUM_NAME_OVERRIDES": {
        "JobStatusEnum": "outfits.models.JobStatus",
    },
}


from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _


def _perm(codename):
    return lambda request: request.user.has_perm(codename)


UNFOLD = {
    "SITE_TITLE": "Khzanti Admin",
    "SITE_HEADER": "Khzanti",
    "SITE_LOGO": {
        "light": SITE_LOGO_URL,
        "dark": SITE_LOGO_DARK_URL,
    },
    "SITE_SYMBOL": "shield_person",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "BORDER_RADIUS": "8px",
    "DASHBOARD_CALLBACK": "accounts.dashboard.dashboard_callback",
    "COLORS": {
        "primary": {
            # Ramp built around brand primary #A37E2C
            "50": "oklch(95.6% 0.005 83.692)",
            "100": "oklch(92.6% 0.013 83.692)",
            "200": "oklch(88.3% 0.025 83.692)",
            "300": "oklch(81.0% 0.048 83.692)",
            "400": "oklch(69.9% 0.082 83.692)",
            "500": "oklch(61.4% 0.108 83.692)",
            "600": "oklch(54.6% 0.117 83.692)",
            "700": "oklch(48.6% 0.108 83.692)",
            "800": "oklch(42.9% 0.089 83.692)",
            "900": "oklch(37.3% 0.072 83.692)",
            "950": "oklch(28.5% 0.061 83.692)",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": _("Navigation"),
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": _("Dashboard"),
                        "icon": "space_dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                    {
                        "title": _("Settings"),
                        "icon": "settings",
                        "link": reverse_lazy("admin:core_sitesettings_change"),
                        "permission": _perm("core.view_sitesettings"),
                    },
                ],
            },
            {
                "title": _("Accounts"),
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": _("Users"),
                        "icon": "person",
                        "link": reverse_lazy("admin:accounts_user_changelist"),
                        "permission": _perm("accounts.view_user"),
                    },
                    {
                        "title": _("Aesthetics"),
                        "icon": "styler",
                        "link": reverse_lazy("admin:accounts_aesthetic_changelist"),
                        "permission": _perm("accounts.view_aesthetic"),
                    },
                    # {
                    #     "title": _("OTP Codes"),
                    #     "icon": "pin",
                    #     "link": reverse_lazy("admin:accounts_otp_changelist"),
                    #     "permission": _perm("accounts.view_otp"),
                    # },
                    # {
                    #     "title": _("Password Reset Tokens"),
                    #     "icon": "key",
                    #     "link": reverse_lazy(
                    #         "admin:accounts_passwordresettoken_changelist"
                    #     ),
                    #     "permission": _perm(
                    #         "accounts.view_passwordresettoken"
                    #     ),
                    # },
                ],
            },
            {
                "title": _("Wardrobe"),
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": _("Categories"),
                        "icon": "checkroom",
                        "link": reverse_lazy(
                            "admin:wardrobe_category_changelist"
                        ),
                        "permission": _perm("wardrobe.view_category"),
                    },
                ],
            },
            {
                "title": _("Style News"),
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": _("Posts"),
                        "icon": "article",
                        "link": reverse_lazy("admin:news_post_changelist"),
                        "permission": _perm("news.view_post"),
                    },
                ],
            },
            {
                "title": _("Authentication"),
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": _("Groups"),
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                        "permission": _perm("auth.view_group"),
                    },
                ],
            },
        ],
    },
}