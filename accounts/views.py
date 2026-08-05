from django.conf import settings
from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import OTP, PasswordResetToken, User
from .serializers import (
    ChangePasswordSerializer,
    DeleteAccountSerializer,
    ForgotPasswordSerializer,
    LoginSerializer,
    RegisterSerializer,
    ResendOTPSerializer,
    ResetPasswordSerializer,
    UpdateProfileSerializer,
    UserSerializer,
    VerifyEmailSerializer,
    VerifyResetOTPSerializer,
)
from .utils import send_otp_email


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "signup"

    @extend_schema(request=RegisterSerializer, responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                "detail": "Registration successful. Please check your email for the verification code.",
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify_reset_otp"

    @extend_schema(request=VerifyEmailSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        otp_obj = serializer.validated_data["otp_obj"]

        with transaction.atomic():
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified"])

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "detail": "Email verified successfully.",
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class ResendOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "resend_otp"

    @extend_schema(request=ResendOTPSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        send_otp_email(serializer.validated_data["user"], serializer.validated_data["purpose"])

        return Response({"detail": "OTP sent successfully."}, status=status.HTTP_200_OK)


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        return Response(
            {
                "detail": "Login successful.",
                "access": data["access"],
                "refresh": data["refresh"],
                "user": UserSerializer(data["user"]).data,
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=inline_serializer(name="LogoutRequest", fields={"refresh": OpenApiTypes.STR}),
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response({"detail": "Refresh token is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            return Response({"detail": "Invalid or already-expired token."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Logged out successfully."}, status=status.HTTP_200_OK)


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "forgot_password"

    @extend_schema(request=ForgotPasswordSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user = User.objects.get(email__iexact=serializer.validated_data["email"])
            send_otp_email(user, OTP.PURPOSE_PASSWORD_RESET)
        except User.DoesNotExist:
            pass  # silently ignore — prevent user enumeration

        return Response(
            {"detail": "If an account with that email exists, a password reset code has been sent."},
            status=status.HTTP_200_OK,
        )


class VerifyResetOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify_reset_otp"

    @extend_schema(request=VerifyResetOTPSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = VerifyResetOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        otp_obj = serializer.validated_data["otp_obj"]

        with transaction.atomic():
            otp_obj.is_used = True
            otp_obj.save(update_fields=["is_used"])
            reset_token, _ = PasswordResetToken.create_for_user(user)

        expiry = getattr(settings, "PASSWORD_RESET_TOKEN_EXPIRY_MINUTES", 10)
        return Response(
            {
                "detail": "OTP verified.",
                "reset_token": reset_token,
                "expires_in_minutes": expiry,
            },
            status=status.HTTP_200_OK,
        )


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=ResetPasswordSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        token_obj = serializer.validated_data["token_obj"]

        with transaction.atomic():
            token_obj.is_used = True
            token_obj.save(update_fields=["is_used"])
            user.set_password(serializer.validated_data["new_password"])
            user.save(update_fields=["password"])

        return Response(
            {"detail": "Password reset successfully. You can now log in."},
            status=status.HTTP_200_OK,
        )


class DeleteAccountView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=DeleteAccountSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = DeleteAccountSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        request.user.delete()

        return Response(
            {"detail": "Your account has been permanently deleted."},
            status=status.HTTP_200_OK,
        )


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserSerializer})
    def get(self, request):
        return Response(UserSerializer(request.user).data, status=status.HTTP_200_OK)

    @extend_schema(request=UpdateProfileSerializer, responses={200: UserSerializer})
    def patch(self, request):
        serializer = UpdateProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(UserSerializer(user).data, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=ChangePasswordSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])

        return Response(
            {"detail": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )
