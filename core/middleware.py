import logging
from django.db import DatabaseError, IntegrityError
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class ApiExceptionMiddleware:
    """
    Middleware that intercepts unhandled exceptions for API requests under /api/v1/*
    and guarantees a structured JSON error response instead of a Django HTML debug traceback page.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        # Only intercept API paths (/api/v1/*) or requests expecting JSON
        if not (request.path.startswith("/api/v1/") or request.headers.get("accept") == "application/json"):
            return None

        logger.exception("ApiExceptionMiddleware caught exception on %s %s: %s", request.method, request.path, exception)

        if isinstance(exception, IntegrityError):
            err_str = str(exception).lower()
            if any(k in err_str for k in ("unique", "duplicate", "already exists")):
                code = 409
                message = (
                    "This outfit has already been saved."
                    if ("saved_outfit" in err_str or "outfit" in err_str)
                    else "This record already exists or violates a unique constraint."
                )
            else:
                code = 500
                message = "A database error occurred."
        elif isinstance(exception, DatabaseError):
            code = 500
            message = "A database error occurred."
        else:
            code = 500
            message = "An unexpected server error occurred."

        return JsonResponse(
            {
                "status": "error",
                "code": code,
                "message": message,
                "data": None,
            },
            status=code,
            content_type="application/json",
        )
