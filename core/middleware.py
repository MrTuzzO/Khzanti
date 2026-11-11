import logging
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError
from django.http import Http404, JsonResponse
from rest_framework.exceptions import APIException

from core.exceptions import get_integrity_error_detail, get_safe_unexpected_exception_detail

logger = logging.getLogger(__name__)


def is_api_request(request):
    """
    Determines whether an incoming request targets the JSON API
    or expects a JSON response, versus an admin or browser-facing HTML page.
    """
    if not request:
        return False
    path = getattr(request, "path_info", None) or getattr(request, "path", "")
    if path.startswith("/api/"):
        return True
    accept = (
        request.headers.get("accept", "")
        if hasattr(request, "headers")
        else request.META.get("HTTP_ACCEPT", "")
    )
    content_type = getattr(request, "content_type", "") or request.META.get("CONTENT_TYPE", "")
    return "application/json" in accept or "application/json" in content_type


class ApiExceptionMiddleware:
    """
    Middleware that intercepts unhandled exceptions for API requests under /api/*
    (or requests expecting JSON) and guarantees a native JSON error response
    ({"detail": "..."}) instead of a Django HTML debug traceback page,
    while preserving standard HTML error pages for admin and browser-facing requests.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        # Only intercept API paths (/api/*) or requests expecting JSON
        if not is_api_request(request):
            return None

        logger.exception(
            "ApiExceptionMiddleware caught unhandled exception on %s %s: %s",
            getattr(request, "method", "UNKNOWN"),
            getattr(request, "path", ""),
            exception,
        )

        if isinstance(exception, Http404):
            return JsonResponse({"detail": "Not found."}, status=404)

        if isinstance(exception, PermissionDenied):
            return JsonResponse({"detail": "Permission denied."}, status=403)

        if isinstance(exception, IntegrityError):
            code, detail = get_integrity_error_detail(exception)
            return JsonResponse({"detail": detail}, status=code)

        if isinstance(exception, DatabaseError):
            return JsonResponse({"detail": "A database error occurred."}, status=500)

        # Trusted application exceptions (standard DRF APIException subclasses, including ServiceError)
        if isinstance(exception, APIException):
            code = getattr(exception, "status_code", 500)
            detail = exception.detail
            if isinstance(detail, (list, tuple)) and len(detail) == 1:
                detail = detail[0]
            elif isinstance(detail, dict):
                return JsonResponse(detail, status=code)
            return JsonResponse({"detail": str(detail)}, status=code)

        # Unexpected exceptions (including arbitrary non-API exceptions with a .detail attribute) are sanitized
        detail = get_safe_unexpected_exception_detail(exception)
        return JsonResponse({"detail": detail}, status=500)
