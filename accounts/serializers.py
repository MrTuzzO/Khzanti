import json
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import MAX_AESTHETICS_PER_PROFILE, Aesthetic, CustomerProfile, OTP, PasswordResetToken, User
from .utils import send_otp_email


class UserSerializer(serializers.ModelSerializer):
    is_profile_completed = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "name", "email", "profile_image", "is_email_verified", "is_profile_completed", "created_at")
        read_only_fields = fields

    def get_is_profile_completed(self, obj) -> bool:
        return getattr(getattr(obj, "customer_profile", None), "is_completed", False)


class UpdateProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("name", "profile_image")
        extra_kwargs = {
            "name": {"required": False},
            "profile_image": {"required": False},
        }


class AestheticSerializer(serializers.ModelSerializer):
    class Meta:
        model = Aesthetic
        fields = ("id", "name", "image")
        read_only_fields = fields


class CompleteProfileSerializer(serializers.ModelSerializer):
    aesthetics = serializers.PrimaryKeyRelatedField(
        queryset=Aesthetic.objects.filter(is_active=True),
        many=True,
        required=False,
    )
    # Writable fields that map to the related User model
    profile_image = serializers.ImageField(required=False, allow_null=True)
    name = serializers.CharField(required=False, max_length=150)

    class Meta:
        model = CustomerProfile
        fields = ("name", "age", "gender", "height", "body_type", "country", "aesthetics", "profile_image")
        extra_kwargs = {
            "age": {"required": False},
            "gender": {"required": False},
            "height": {"required": False},
            "body_type": {"required": False},
            "country": {"required": False},
        }

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        # Read user fields from the related user
        user = instance.user
        request = self.context.get("request")
        rep["name"] = user.name
        if user.profile_image:
            rep["profile_image"] = (
                request.build_absolute_uri(user.profile_image.url)
                if request
                else user.profile_image.url
            )
        else:
            rep["profile_image"] = None
        return rep

    def to_internal_value(self, data):
        # When the request is multipart/form-data, `aesthetics` arrives as a
        # JSON string "[1,2,3]" instead of repeated form keys.
        # We convert to a flat dict via QueryDict.dict() (gives {key: last_value})
        # so DRF's ManyRelatedField receives the parsed list via dict.get().
        aesthetics_val = data.get("aesthetics")
        if isinstance(aesthetics_val, str):
            try:
                parsed = json.loads(aesthetics_val)
                if isinstance(parsed, list):
                    plain = data.dict() if hasattr(data, "dict") else dict(data)
                    plain["aesthetics"] = parsed
                    data = plain
            except (ValueError, TypeError):
                pass
        return super().to_internal_value(data)

    def validate_aesthetics(self, value):
        if len(value) > MAX_AESTHETICS_PER_PROFILE:
            raise serializers.ValidationError(f"Select up to {MAX_AESTHETICS_PER_PROFILE} aesthetics.")
        return value

    def update(self, instance, validated_data):
        aesthetics = validated_data.pop("aesthetics", None)
        profile_image = validated_data.pop("profile_image", None)
        name = validated_data.pop("name", None)

        # Update User fields if provided
        user_update_fields = []
        if profile_image is not None:
            instance.user.profile_image = profile_image
            user_update_fields.append("profile_image")
        if name is not None:
            instance.user.name = name
            user_update_fields.append("name")
        if user_update_fields:
            instance.user.save(update_fields=user_update_fields)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Mark complete once the required fields are present.
        instance.is_completed = all(
            [
                instance.age,
                instance.gender,
                instance.height,
                instance.body_type,
                instance.country,
            ]
        )
        instance.save()

        if aesthetics is not None:
            instance.aesthetics.set(aesthetics)

        return instance


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    new_password_confirm = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Incorrect current password.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password": "Passwords do not match."})
        return attrs


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


class DeleteAccountSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Incorrect password.")
        return value


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
