import secrets

from django.conf import settings
from django.core.mail import send_mail

from .models import OTP


def generate_otp_code() -> str:
    return f"{secrets.randbelow(900_000) + 100_000}"


def send_otp_email(user, purpose: str) -> OTP:
    OTP.objects.filter(user=user, purpose=purpose, is_used=False).update(is_used=True)

    code = generate_otp_code()
    otp = OTP.objects.create(user=user, code=code, purpose=purpose)

    expiry = getattr(settings, "OTP_EXPIRY_MINUTES", 10)

    if purpose == OTP.PURPOSE_EMAIL_VERIFICATION:
        subject = "Verify your email"
        body = (
            f"Hi {user.name},\n\n"
            f"Your email verification code is: {code}\n"
            f"It expires in {expiry} minutes.\n\n"
            "If you did not sign up, please ignore this email."
        )
    else:
        subject = "Reset your password"
        body = (
            f"Hi {user.name},\n\n"
            f"Your password reset code is: {code}\n"
            f"It expires in {expiry} minutes.\n\n"
            "If you did not request this, please ignore this email."
        )

    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return otp
