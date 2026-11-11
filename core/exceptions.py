import logging
import re
from django.conf import settings
from django.db import DatabaseError, IntegrityError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)

# Patterns that might leak sensitive database, credential, filesystem path, or internal implementation details
SENSITIVE_PATTERNS = [
    # Credentials & secrets
    r"password",
    r"secret",
    r"token",
    r"api[_-]?key",
    r"authorization",
    r"bearer\s+",
    r"credential",
    # SQL syntax & database internals
    r"select\s+",
    r"insert\s+",
    r"update\s+",
    r"delete\s+",
    r"from\s+\w+",
    r"table\s+",
    r"column\s+",
    r"where\s+",
    r"drop\s+",
    r"alter\s+",
    r"psycopg",
    r"sqlite",
    r"django\.db",
    # Filesystem paths & environment files
    r"[a-zA-Z]:[\\/]",
    r"[\\/](home|var|usr|etc|opt|tmp|app|env|venv|site-packages)[\\/]",
    r"\.py\b",
    r"\.env\b",
    # Internal implementation & tracebacks
    r"traceback",
    r"line\s+\d+",
    r"file\s+[\"']",
]
SENSITIVE_REGEX = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)


class ServiceError(APIException):
    """
    Base exception for handled external service & pipeline failures.
    - `detail`: Safe, user-facing error message (returned in production & dev).
    - `debug_detail`: Technical/raw error detail (internal logging).
    """
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "A service error occurred."
    default_code = "service_error"

    def __init__(self, detail=None, debug_detail=None, status_code=502):
        if status_code is not None:
            self.status_code = status_code
        self.debug_detail = debug_detail
        super().__init__(detail=detail)


def is_safe_debug_message(msg: str) -> bool:
    """
    Checks whether an error message is safe to expose in DEBUG mode without
    leaking credentials, SQL queries, filesystem paths, or internal implementation details.
    """
    if not msg or not isinstance(msg, str):
        return False
    return not bool(SENSITIVE_REGEX.search(msg))


def get_safe_unexpected_exception_detail(exc) -> str:
    """
    Returns a safe user-facing message for unexpected exceptions.
    Arbitrary exceptions with a .detail attribute are not automatically trusted.
    In DEBUG mode, inspects exc.detail or str(exc) and returns it only if it passes
    sensitivity sanitization. In production mode (or when sensitive), returns a safe generic message.
    """
    if getattr(settings, "DEBUG", False):
        raw_msg = ""
        if hasattr(exc, "detail") and exc.detail and isinstance(exc.detail, (str, int, float)):
            raw_msg = str(exc.detail).strip()
        if not raw_msg:
            raw_msg = str(exc).strip()

        if raw_msg and is_safe_debug_message(raw_msg):
            return raw_msg

    return "An unexpected server error occurred."


def get_integrity_error_detail(exc) -> tuple[int, str]:
    """
    Determines whether an IntegrityError represents a duplicate key / unique constraint
    violation across PostgreSQL and SQLite backends, returning (status_code, safe_detail).
    """
    err_str = str(exc).lower()
    cause = getattr(exc, "__cause__", None)
    if cause:
        err_str += " " + str(cause).lower()

    if any(k in err_str for k in ("unique", "duplicate", "already exists")):
        if "saved_outfit" in err_str or "outfit" in err_str:
            detail = "This outfit has already been saved."
        else:
            detail = "This record already exists or violates a unique constraint."
        return status.HTTP_409_CONFLICT, detail

    return status.HTTP_500_INTERNAL_SERVER_ERROR, "A database error occurred."


def custom_exception_handler(exc, context):
    """
    Global DRF Exception Handler.
    Preserves DRF's native error response formats:
      - Field validation errors: {"<field>": ["<message>"]}
      - Detail errors: {"detail": "<message>"}
    Handles unexpected Python/database exceptions when DRF returns None,
    logging full tracebacks and returning safe JSON 500/409 responses.
    """
    response = exception_handler(exc, context)

    if response is not None:
        # Native DRF exception handled (APIException, Http404, PermissionDenied).
        # Original status code and DRF error structure are preserved.
        # Normalize {"detail": ["..."]} or single-item non-field lists to {"detail": "..."}
        # to ensure standard DRF detail string format as requested in the API specification.
        if isinstance(response.data, dict) and "detail" in response.data:
            if isinstance(response.data["detail"], (list, tuple)) and len(response.data["detail"]) == 1:
                response.data["detail"] = response.data["detail"][0]
        elif isinstance(response.data, (list, tuple)) and len(response.data) == 1 and not isinstance(response.data[0], (dict, list)):
            response.data = {"detail": response.data[0]}
        return response

    # Handle unexpected exceptions that DRF's default exception_handler returns None for
    view_name = context.get("view").__class__.__name__ if context and context.get("view") else "UnknownView"
    logger.exception("Unhandled API exception in view [%s]: %s", view_name, exc)

    if isinstance(exc, IntegrityError):
        code, detail = get_integrity_error_detail(exc)
        return Response({"detail": detail}, status=code)

    if isinstance(exc, DatabaseError):
        return Response({"detail": "A database error occurred."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Unexpected Python exceptions (even if exposing a .detail attribute) are sanitized
    detail = get_safe_unexpected_exception_detail(exc)
    return Response({"detail": detail}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
