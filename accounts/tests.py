from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import OTP, PasswordResetToken
from accounts.serializers import RegisterSerializer

User = get_user_model()


class AuthenticationLoginLogoutAPITestCase(APITestCase):
    def setUp(self):
        self.password = "StrongPass123!"
        self.user = User.objects.create_user(
            email="testuser@example.com",
            name="Test User",
            password=self.password,
            is_email_verified=True,
            is_active=True,
        )

    def test_normal_email_and_correct_password(self):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "testuser@example.com", "password": self.password},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        self.assertEqual(resp.data["user"]["email"], "testuser@example.com")

    def test_uppercase_and_mixed_case_email_and_correct_password(self):
        # All uppercase
        resp_upper = self.client.post(
            "/api/v1/auth/login/",
            {"email": "TESTUSER@EXAMPLE.COM", "password": self.password},
        )
        self.assertEqual(resp_upper.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp_upper.data)
        self.assertIn("refresh", resp_upper.data)

        # Mixed case
        resp_mixed = self.client.post(
            "/api/v1/auth/login/",
            {"email": "TestUser@Example.Com", "password": self.password},
        )
        self.assertEqual(resp_mixed.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp_mixed.data)
        self.assertIn("refresh", resp_mixed.data)

    def test_email_with_surrounding_whitespace_and_correct_password(self):
        # Leading and trailing spaces
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "   testuser@example.com   ", "password": self.password},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

        # Mixed case with whitespace
        resp_mixed_ws = self.client.post(
            "/api/v1/auth/login/",
            {"email": "  TestUser@Example.COM  ", "password": self.password},
        )
        self.assertEqual(resp_mixed_ws.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp_mixed_ws.data)
        self.assertIn("refresh", resp_mixed_ws.data)

    def test_incorrect_password_remains_rejected(self):
        # Exact email with wrong password
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "testuser@example.com", "password": "WrongPassword123!"},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        error_msg = resp.data.get("message") or resp.data.get("detail")
        self.assertEqual(error_msg, "Invalid email or password.")

        # Mixed-case email with wrong password
        resp_mixed = self.client.post(
            "/api/v1/auth/login/",
            {"email": "TESTUSER@EXAMPLE.COM", "password": "WrongPassword123!"},
        )
        self.assertEqual(resp_mixed.status_code, status.HTTP_400_BAD_REQUEST)
        error_mixed_msg = resp_mixed.data.get("message") or resp_mixed.data.get("detail")
        self.assertEqual(error_mixed_msg, "Invalid email or password.")

    def test_invalid_or_nonexistent_email_remains_rejected(self):
        # Nonexistent email
        resp_nonexistent = self.client.post(
            "/api/v1/auth/login/",
            {"email": "nonexistent@example.com", "password": self.password},
        )
        self.assertEqual(resp_nonexistent.status_code, status.HTTP_400_BAD_REQUEST)
        error_msg = resp_nonexistent.data.get("message") or resp_nonexistent.data.get("detail")
        self.assertEqual(error_msg, "Invalid email or password.")

        # Malformed email format
        resp_invalid_format = self.client.post(
            "/api/v1/auth/login/",
            {"email": "not-an-email", "password": self.password},
        )
        self.assertEqual(resp_invalid_format.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue("email" in str(resp_invalid_format.data).lower())

    def test_login_after_logout_workflow(self):
        # 1. Login initial
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "testuser@example.com", "password": self.password},
        )
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)
        access_token = login_resp.data["access"]
        refresh_token = login_resp.data["refresh"]

        # 2. Authenticated user logs out (blacklisting refresh token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        logout_resp = self.client.post(
            "/api/v1/auth/logout/",
            {"refresh": refresh_token},
        )
        self.assertEqual(logout_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(logout_resp.data["detail"], "Logged out successfully.")

        # Clear credentials
        self.client.credentials()

        # 3. Subsequent login with mixed case and whitespace succeeds
        relogin_resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "  TestUser@Example.COM  ", "password": self.password},
        )
        self.assertEqual(relogin_resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", relogin_resp.data)
        self.assertIn("refresh", relogin_resp.data)

    def test_inactive_and_unverified_user_rejected(self):
        # Inactive user
        inactive_user = User.objects.create_user(
            email="inactive@example.com",
            name="Inactive User",
            password=self.password,
            is_email_verified=True,
            is_active=False,
        )
        resp_inactive = self.client.post(
            "/api/v1/auth/login/",
            {"email": "inactive@example.com", "password": self.password},
        )
        self.assertEqual(resp_inactive.status_code, status.HTTP_400_BAD_REQUEST)

        # Unverified user
        unverified_user = User.objects.create_user(
            email="unverified@example.com",
            name="Unverified User",
            password=self.password,
            is_email_verified=False,
            is_active=True,
        )
        resp_unverified = self.client.post(
            "/api/v1/auth/login/",
            {"email": "unverified@example.com", "password": self.password},
        )
        self.assertEqual(resp_unverified.status_code, status.HTTP_400_BAD_REQUEST)
        error_msg = resp_unverified.data.get("message") or resp_unverified.data.get("detail")
        self.assertIn("Email not verified", error_msg)

    def test_case_collision_disambiguation(self):
        # Simulate legacy case collision in DB via mock (as DB constraint prevents creating duplicates)
        user_lower = User.objects.create_user(
            email="collision_disambig@example.com",
            name="User Lower",
            password="LowerPass123!",
            is_email_verified=True,
            is_active=True,
        )
        user_upper = User(
            id=9999,
            email="COLLISION_DISAMBIG@EXAMPLE.COM",
            username="collision_upper",
            name="User Upper",
            is_email_verified=True,
            is_active=True,
        )

        with patch("accounts.serializers.User.objects.filter") as mock_filter:
            mock_filter.return_value = [user_lower, user_upper]

            # 1. Exact match for lower authenticates lower
            resp_lower = self.client.post(
                "/api/v1/auth/login/",
                {"email": "collision_disambig@example.com", "password": "LowerPass123!"},
            )
            self.assertEqual(resp_lower.status_code, status.HTTP_200_OK)
            self.assertEqual(resp_lower.data["user"]["id"], user_lower.id)

            # 2. Ambiguous mixed-case input without exact match is rejected safely
            resp_ambiguous = self.client.post(
                "/api/v1/auth/login/",
                {"email": "Collision_Disambig@example.com", "password": "LowerPass123!"},
            )
            self.assertEqual(resp_ambiguous.status_code, status.HTTP_400_BAD_REQUEST)


class RegistrationAndAccountSecurityAPITestCase(APITestCase):
    def test_registration_normalizes_email_and_enforces_password_strength(self):
        # Weak short password (< 8 chars) rejected by Django's MinimumLengthValidator
        resp_weak = self.client.post(
            "/api/v1/auth/signup/",
            {
                "name": "Weak User",
                "email": "weak@example.com",
                "password": "short",
            },
        )
        self.assertEqual(resp_weak.status_code, status.HTTP_400_BAD_REQUEST)

        # Valid registration with mixed case and whitespace
        resp_valid = self.client.post(
            "/api/v1/auth/signup/",
            {
                "name": "New Person",
                "email": "   NewPerson@Example.COM   ",
                "password": "StrongPassword123!",
            },
        )
        self.assertEqual(resp_valid.status_code, status.HTTP_201_CREATED)

        created_user = User.objects.get(email="newperson@example.com")
        self.assertEqual(created_user.email, "newperson@example.com")
        self.assertFalse(created_user.is_email_verified)

    def test_duplicate_registration_rejected_case_insensitively(self):
        User.objects.create_user(
            email="existing@example.com",
            name="Existing",
            password="StrongPass123!",
        )

        # Attempt to register with uppercase variant
        resp_dup = self.client.post(
            "/api/v1/auth/signup/",
            {
                "name": "Duplicate Person",
                "email": "EXISTING@EXAMPLE.COM",
                "password": "StrongPass123!",
            },
        )
        self.assertEqual(resp_dup.status_code, status.HTTP_400_BAD_REQUEST)

    def test_email_verification_otp_flow_and_attempt_limit(self):
        user = User.objects.create_user(
            email="verify_me@example.com",
            name="Verify Me",
            password="StrongPass123!",
            is_email_verified=False,
        )
        otp = OTP.objects.create(
            user=user,
            code="123456",
            purpose=OTP.PURPOSE_EMAIL_VERIFICATION,
        )

        # Wrong OTP code increments attempts
        resp_wrong = self.client.post(
            "/api/v1/auth/verify-email/",
            {"email": "verify_me@example.com", "otp": "000000"},
        )
        self.assertEqual(resp_wrong.status_code, status.HTTP_400_BAD_REQUEST)
        otp.refresh_from_db()
        self.assertEqual(otp.attempts, 1)

        # Correct OTP verifies email
        resp_correct = self.client.post(
            "/api/v1/auth/verify-email/",
            {"email": "VERIFY_ME@EXAMPLE.COM", "otp": "123456"},
        )
        self.assertEqual(resp_correct.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)
        otp.refresh_from_db()
        self.assertTrue(otp.is_used)

        # Re-use of OTP is rejected
        resp_reuse = self.client.post(
            "/api/v1/auth/verify-email/",
            {"email": "verify_me@example.com", "otp": "123456"},
        )
        self.assertEqual(resp_reuse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_reset_flow_and_anti_enumeration(self):
        user = User.objects.create_user(
            email="reset_target@example.com",
            name="Reset Target",
            password="OldPassword123!",
            is_email_verified=True,
        )

        # Nonexistent user does not leak existence
        resp_anon = self.client.post(
            "/api/v1/auth/forgot-password/",
            {"email": "ghost@example.com"},
        )
        self.assertEqual(resp_anon.status_code, status.HTTP_200_OK)

        # Existing user forgot password creates OTP
        resp_forgot = self.client.post(
            "/api/v1/auth/forgot-password/",
            {"email": "RESET_TARGET@EXAMPLE.COM"},
        )
        self.assertEqual(resp_forgot.status_code, status.HTTP_200_OK)
        otp = OTP.objects.filter(user=user, purpose=OTP.PURPOSE_PASSWORD_RESET).latest("created_at")

        # Verify OTP returns reset token
        resp_verify_otp = self.client.post(
            "/api/v1/auth/verify-reset-otp/",
            {"email": "reset_target@example.com", "otp": otp.code},
        )
        self.assertEqual(resp_verify_otp.status_code, status.HTTP_200_OK)
        reset_token = resp_verify_otp.data["reset_token"]

        # Reset password with token
        resp_reset = self.client.post(
            "/api/v1/auth/reset-password/",
            {
                "reset_token": reset_token,
                "new_password": "NewStrongPass123!",
                "new_password_confirm": "NewStrongPass123!",
            },
        )
        self.assertEqual(resp_reset.status_code, status.HTTP_200_OK)

        # Authenticate with new password
        resp_login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "reset_target@example.com", "password": "NewStrongPass123!"},
        )
        self.assertEqual(resp_login.status_code, status.HTTP_200_OK)

        # Old password rejected
        resp_old = self.client.post(
            "/api/v1/auth/login/",
            {"email": "reset_target@example.com", "password": "OldPassword123!"},
        )
        self.assertEqual(resp_old.status_code, status.HTTP_400_BAD_REQUEST)

    def test_database_level_case_insensitive_unique_constraint_enforced(self):
        User.objects.create(
            email="db_unique_test@example.com",
            username="db_unique_1",
            name="User One",
        )
        # Direct database insert with case-variant duplicate email MUST raise IntegrityError
        with self.assertRaises(IntegrityError):
            User.objects.create(
                email="DB_UNIQUE_TEST@EXAMPLE.COM",
                username="db_unique_2",
                name="User Two",
            )

    def test_concurrent_registration_integrity_error_handled_cleanly(self):
        # Simulate race condition: serializer.is_valid() passes, but DB raises IntegrityError on save
        with patch.object(RegisterSerializer, "save", side_effect=IntegrityError("duplicate key")):
            resp = self.client.post(
                "/api/v1/auth/signup/",
                {
                    "name": "Race User",
                    "email": "race@example.com",
                    "password": "StrongPassword123!",
                },
            )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already exists", str(resp.data).lower())

    def test_atomic_single_use_otp_concurrency(self):
        user = User.objects.create_user(
            email="single_use_otp@example.com",
            name="Single Use OTP",
            password="StrongPass123!",
            is_email_verified=False,
        )
        OTP.objects.create(
            user=user,
            code="654321",
            purpose=OTP.PURPOSE_EMAIL_VERIFICATION,
        )

        # First verification succeeds
        resp1 = self.client.post(
            "/api/v1/auth/verify-email/",
            {"email": "single_use_otp@example.com", "otp": "654321"},
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)

        # Second verification fails (already used)
        resp2 = self.client.post(
            "/api/v1/auth/verify-email/",
            {"email": "single_use_otp@example.com", "otp": "654321"},
        )
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_reset_revokes_outstanding_refresh_tokens(self):
        user = User.objects.create_user(
            email="token_revoke@example.com",
            name="Token Revoke",
            password="OldPassword123!",
            is_email_verified=True,
        )
        refresh = RefreshToken.for_user(user)
        self.assertTrue(OutstandingToken.objects.filter(user=user).exists())
        self.assertFalse(BlacklistedToken.objects.filter(token__user=user).exists())

        raw_token, _ = PasswordResetToken.create_for_user(user)
        resp_reset = self.client.post(
            "/api/v1/auth/reset-password/",
            {
                "reset_token": raw_token,
                "new_password": "BrandNewPassword123!",
                "new_password_confirm": "BrandNewPassword123!",
            },
        )
        self.assertEqual(resp_reset.status_code, status.HTTP_200_OK)

        # All outstanding tokens for user must now be blacklisted
        self.assertTrue(BlacklistedToken.objects.filter(token__user=user).exists())

        # Attempting to refresh with old refresh token fails with 401
        resp_refresh = self.client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": str(refresh)},
        )
        self.assertEqual(resp_refresh.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_change_password_revokes_outstanding_refresh_tokens(self):
        user = User.objects.create_user(
            email="change_pwd_revoke@example.com",
            name="Change Pwd",
            password="OldPassword123!",
            is_email_verified=True,
        )
        refresh = RefreshToken.for_user(user)
        self.assertTrue(OutstandingToken.objects.filter(user=user).exists())
        self.assertFalse(BlacklistedToken.objects.filter(token__user=user).exists())

        self.client.force_authenticate(user=user)
        resp = self.client.post(
            "/api/v1/auth/change-password/",
            {
                "old_password": "OldPassword123!",
                "new_password": "BrandNewPassword123!",
                "new_password_confirm": "BrandNewPassword123!",
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Blacklisted now
        self.assertTrue(BlacklistedToken.objects.filter(token__user=user).exists())

        # Refresh token invalid
        self.client.force_authenticate(user=None)
        resp_refresh = self.client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": str(refresh)},
        )
        self.assertEqual(resp_refresh.status_code, status.HTTP_401_UNAUTHORIZED)
