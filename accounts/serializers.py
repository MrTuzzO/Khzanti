from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import OTP, PasswordResetToken, User
from .utils import send_otp_email


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "name", "email", "avatar_url", "is_email_verified", "created_at")
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ("name", "email", "password")

    def validate_email(self, value):
        value = User.objects.normalize_email(value)
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data["email"],
            name=validated_data["name"],
            password=validated_data["password"],
        )
        send_otp_email(user, OTP.PURPOSE_EMAIL_VERIFICATION)
        return user


class VerifyEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6, min_length=6)

    def validate(self, attrs):
        generic_error = {"otp": "Invalid or expired OTP."}

        try:
            user = User.objects.get(email__iexact=attrs["email"])
        except User.DoesNotExist:
            raise serializers.ValidationError(generic_error)

        if user.is_email_verified:
            raise serializers.ValidationError({"email": "Email is already verified."})

        otp_obj = (
            OTP.objects
            .filter(user=user, purpose=OTP.PURPOSE_EMAIL_VERIFICATION, is_used=False)
            .order_by("-created_at")
            .first()
        )

        if not otp_obj or otp_obj.is_expired or otp_obj.attempts_exceeded:
            raise serializers.ValidationError(generic_error)

        if otp_obj.code != attrs["otp"]:
            otp_obj.attempts += 1
            update_fields = ["attempts"]
            if otp_obj.attempts >= getattr(settings, "OTP_MAX_ATTEMPTS", 5):
                otp_obj.is_used = True
                update_fields.append("is_used")
            otp_obj.save(update_fields=update_fields)
            raise serializers.ValidationError(generic_error)

        attrs["user"] = user
        attrs["otp_obj"] = otp_obj
        return attrs


class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    purpose = serializers.ChoiceField(choices=OTP.PURPOSE_CHOICES)

    def validate(self, attrs):
        try:
            user = User.objects.get(email__iexact=attrs["email"])
        except User.DoesNotExist:
            raise serializers.ValidationError({"email": "User not found."})

        if attrs["purpose"] == OTP.PURPOSE_EMAIL_VERIFICATION and user.is_email_verified:
            raise serializers.ValidationError({"email": "Email is already verified."})

        attrs["user"] = user
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["email"],
            password=attrs["password"],
        )
        if not user:
            raise serializers.ValidationError({"detail": "Invalid email or password."})
        if not user.is_active:
            raise serializers.ValidationError({"detail": "Account is disabled."})
        if not user.is_email_verified:
            raise serializers.ValidationError({"detail": "Email not verified. Please verify your email first."})

        refresh = RefreshToken.for_user(user)
        return {
            "user": user,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class VerifyResetOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6, min_length=6)

    def validate(self, attrs):
        generic_error = {"otp": "Invalid or expired OTP."}

        try:
            user = User.objects.get(email__iexact=attrs["email"])
        except User.DoesNotExist:
            raise serializers.ValidationError(generic_error)

        otp_obj = (
            OTP.objects
            .filter(user=user, purpose=OTP.PURPOSE_PASSWORD_RESET, is_used=False)
            .order_by("-created_at")
            .first()
        )

        if not otp_obj or otp_obj.is_expired or otp_obj.attempts_exceeded:
            raise serializers.ValidationError(generic_error)

        if otp_obj.code != attrs["otp"]:
            otp_obj.attempts += 1
            update_fields = ["attempts"]
            if otp_obj.attempts >= getattr(settings, "OTP_MAX_ATTEMPTS", 5):
                otp_obj.is_used = True
                update_fields.append("is_used")
            otp_obj.save(update_fields=update_fields)
            raise serializers.ValidationError(generic_error)

        attrs["user"] = user
        attrs["otp_obj"] = otp_obj
        return attrs


class ResetPasswordSerializer(serializers.Serializer):
    reset_token = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    new_password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password": "Passwords do not match."})

        token_obj = (
            PasswordResetToken.objects
            .select_related("user")
            .filter(token_hash=PasswordResetToken.hash_token(attrs["reset_token"]), is_used=False)
            .first()
        )
        if not token_obj or token_obj.is_expired:
            raise serializers.ValidationError({"reset_token": "Invalid or expired reset token."})

        attrs["user"] = token_obj.user
        attrs["token_obj"] = token_obj
        return attrs
