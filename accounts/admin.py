from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import OTP, PasswordResetToken, User


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    ordering = ("-created_at",)
    list_display = ("user_header", "is_email_verified", "is_active", "is_staff", "created_at")
    search_fields = ("email", "name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("name", "profile_image")}),
        ("Permissions", {"fields": ("is_email_verified", "is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "name", "password1", "password2")}),
    )
    readonly_fields = ("created_at", "updated_at", "last_login")

    @display(description="User", header=True)
    def user_header(self, obj):
        image = {"path": obj.profile_image.url} if obj.profile_image else None
        initials = (obj.name[:1] if obj.name else obj.email[:1]).upper()
        return obj.name or obj.email, obj.email, initials, image


@admin.register(OTP)
class OTPAdmin(ModelAdmin):
    list_display = ("user", "purpose", "is_used", "attempts", "created_at", "expires_at")
    search_fields = ("user__email",)
    readonly_fields = [f.name for f in OTP._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(ModelAdmin):
    list_display = ("user", "is_used", "created_at", "expires_at")
    search_fields = ("user__email",)
    readonly_fields = [f.name for f in PasswordResetToken._meta.fields]

    def has_add_permission(self, request):
        return False
