import hashlib
import re
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Aesthetic(models.Model):
    name = models.CharField(max_length=50, unique=True)
    image = models.ImageField(upload_to="aesthetics/")
    order = models.PositiveSmallIntegerField(default=0, help_text="Controls display order (lower first).")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "Aesthetic"
        verbose_name_plural = "Aesthetics"

    def __str__(self):
        return self.name


class Gender(models.TextChoices):
    MALE = "male", _("Male")
    FEMALE = "female", _("Female")
    OTHER = "other", _("Other")


class BodyType(models.TextChoices):
    SLIM = "slim", _("Slim")
    ATHLETIC = "athletic", _("Athletic")
    AVERAGE = "average", _("Average")
    CURVY = "curvy", _("Curvy")
    PLUS_SIZE = "plus_size", _("Plus Size")


MAX_AESTHETICS_PER_PROFILE = 3


class UserManager(BaseUserManager):
    def create_user(self, email, name, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        extra_fields.setdefault("username", self._generate_unique_username(email))
        user = self.model(email=email, name=name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def _generate_unique_username(self, email):
        base = re.sub(r"[^a-z0-9_]", "", email.split("@")[0].lower())[:25] or "user"
        username = base
        suffix = 0
        while self.model.objects.filter(username=username).exists():
            suffix += 1
            username = f"{base}{suffix}"
        return username

    def create_superuser(self, email, name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_email_verified", True)
        return self.create_user(email, name, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=30, unique=True, db_index=True)
    profile_image = models.ImageField(upload_to="profile_images/", null=True, blank=True, max_length=500)
    is_email_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return self.email

    def get_full_name(self):
        return self.name

    def get_short_name(self):
        return self.name


class CustomerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer_profile")
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    height = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Height in centimeters.")
    body_type = models.CharField(max_length=20, choices=BodyType.choices, blank=True)
    country = models.CharField(max_length=100, blank=True)
    aesthetics = models.ManyToManyField(Aesthetic, related_name="customer_profiles", blank=True)
    is_completed = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Set once the user finishes the post-signup 'Complete Profile' step.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} profile"


class OTP(models.Model):
    PURPOSE_EMAIL_VERIFICATION = "email_verification"
    PURPOSE_PASSWORD_RESET = "password_reset"
    PURPOSE_CHOICES = [
        (PURPOSE_EMAIL_VERIFICATION, "Email Verification"),
        (PURPOSE_PASSWORD_RESET, "Password Reset"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otps")
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=30, choices=PURPOSE_CHOICES)
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if not self.pk:
            expiry = getattr(settings, "OTP_EXPIRY_MINUTES", 10)
            self.expires_at = timezone.now() + timedelta(minutes=expiry)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def attempts_exceeded(self):
        max_attempts = getattr(settings, "OTP_MAX_ATTEMPTS", 5)
        return self.attempts >= max_attempts

    def __str__(self):
        return f"{self.user.email} – {self.purpose}"


class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    def create_for_user(cls, user):
        cls.objects.filter(user=user, is_used=False).update(is_used=True)

        raw_token = secrets.token_urlsafe(32)
        expiry = getattr(settings, "PASSWORD_RESET_TOKEN_EXPIRY_MINUTES", 10)
        token = cls.objects.create(
            user=user,
            token_hash=cls.hash_token(raw_token),
            expires_at=timezone.now() + timedelta(minutes=expiry),
        )
        return raw_token, token

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.user.email} password reset token"
