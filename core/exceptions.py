import logging
from django.conf import settings
from django.db import DatabaseError, IntegrityError
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class ServiceError(APIException):
    """
    Base exception for handled external service & pipeline failures.
    - `detail`: Safe, user-facing error message (returned in production & dev).
    - `debug_detail`: Technical/raw error detail (included ONLY when settings.DEBUG is True).
    """
    def __init__(self, detail=None, debug_detail=None, status_code=502):
        if status_code is not None:
            self.status_code = status_code
        self.debug_detail = debug_detail
        super().__init__(detail)


def custom_exception_handler(exc, context):
    """
    Global DRF Exception Handler.
    Converts all exceptions (DRF, Database, generic Python) into clean JSON responses:
    {
        "status": "error",
        "code": <HTTP_STATUS_CODE>,
        "message": "<User-friendly error message>",
        "data": null
    }
    """
    response = exception_handler(exc, context)

    if response is None:
        # Handle exceptions that DRF's default exception_handler does not catch
        logger.exception("Unhandled API exception in view context: %s", exc)

        if isinstance(exc, IntegrityError):
            err_str = str(exc).lower()
            if any(k in err_str for k in ("unique", "duplicate", "already exists")):
                code = status.HTTP_409_CONFLICT
                message = (
                    "This outfit has already been saved."
                    if ("saved_outfit" in err_str or "outfit" in err_str)
                    else "This record already exists or violates a unique constraint."
                )
            else:
                code = status.HTTP_500_INTERNAL_SERVER_ERROR
                message = "A database error occurred."

            payload = {
                "status": "error",
                "code": code,
                "message": message,
                "data": None,
            }
            return Response(payload, status=code)

        if isinstance(exc, DatabaseError):
            payload = {
                "status": "error",
                "code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "message": "A database error occurred.",
                "data": None,
            }
            return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Generic unexpected Python exceptions (AttributeError, KeyError, TypeError, ValueError, etc.)
        payload = {
            "status": "error",
            "code": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "message": "An unexpected server error occurred.",
            "data": None,
        }
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Standard DRF APIException handling
    payload = {
        "status": "error",
        "code": response.status_code,
        "message": _extract_message(exc, response.data),
        "data": getattr(exc, "response_data", None),
    }

    if getattr(settings, "DEBUG", False):
        debug_detail = getattr(exc, "debug_detail", None)
        if debug_detail:
            payload["debug_detail"] = str(debug_detail)

    response.data = payload
    return response


def _extract_message(exc, data):
    if isinstance(data, dict) and "detail" in data:
        detail = data["detail"]
        if isinstance(detail, list):
            return _clean(detail[0])
        return _clean(detail)

    if isinstance(exc, ValidationError) and isinstance(data, dict):
        for field, errors in data.items():
            first, path = _get_first_error_with_path(errors)
            if first:
                if field in ("__all__", "non_field_errors"):
                    return first

                field_label = field.replace("_", " ").capitalize()

                if path:
                    nested_label = " → ".join(
                        p.replace("_", " ").capitalize() for p in path
                    )
                    return f"{field_label} → {nested_label}: {first}"

                return f"{field_label}: {first}"

        return "Validation failed."

    if isinstance(data, list) and data:
        return _clean(data[0])

    return _clean(data) if isinstance(data, str) else "An unexpected error occurred."


def _get_first_error_with_path(errors, path=None):
    if path is None:
        path = []

    if isinstance(errors, list):
        for item in errors:
            msg, found_path = _get_first_error_with_path(item, path)
            if msg:
                return msg, found_path

    elif isinstance(errors, dict):
        for key, value in errors.items():
            msg, found_path = _get_first_error_with_path(value, path + [key])
            if msg:
                return msg, found_path

    elif errors:
        return _clean(errors), path

    return None, []


def _clean(value):
    return str(value)
