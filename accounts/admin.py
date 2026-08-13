from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db import models
from django.utils.html import format_html
from unfold.admin import ModelAdmin, StackedInline
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.widgets import UnfoldAdminImageFieldWidget

from .models import Aesthetic, CustomerProfile, OTP, PasswordResetToken, User


class CustomerProfileInline(StackedInline):
    model = CustomerProfile
    filter_horizontal = ("aesthetics",)
    readonly_fields = ("is_completed", "created_at", "updated_at")
    fields = ("age", "gender", "height", "body_type", "country", "aesthetics", "is_completed", "created_at", "updated_at")
    extra = 0
    max_num = 1
    can_delete = False


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    inlines = [CustomerProfileInline]

    ordering = ("-created_at",)
    list_display = ("user_header", "profile_completed", "is_email_verified", "is_active", "is_staff", "created_at")
    list_filter = ("customer_profile__is_completed", "is_email_verified", "is_active", "is_staff")
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

    @display(description="Profile Completed", boolean=True)
    def profile_completed(self, obj):
        return getattr(getattr(obj, "customer_profile", None), "is_completed", False)


@admin.register(Aesthetic)
class AestheticAdmin(ModelAdmin):
    list_display = ("thumbnail_preview", "name", "order", "is_active")
    list_editable = ("order", "is_active")
    search_fields = ("name",)
    ordering = ("order", "name")
    readonly_fields = ("created_at", "updated_at")
    fields = ("name", "image", "order", "is_active", "created_at", "updated_at")
    # accept="image/*" turns on Unfold's built-in image preview above the upload control.
    formfield_overrides = {
        models.ImageField: {"widget": UnfoldAdminImageFieldWidget(attrs={"accept": "image/*"})},
    }

    @display(description="Image")
    def thumbnail_preview(self, obj):
        if not obj.image:
            return "-"
        return format_html(
            '<img src="{}" style="height:40px;width:40px;object-fit:cover;border-radius:6px;" />', obj.image.url
        )


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
