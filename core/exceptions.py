from rest_framework.views import exception_handler
from rest_framework.exceptions import ValidationError


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        response.data = {
            "status": "error",
            "code": response.status_code,
            "message": _extract_message(exc, response.data),
            "data": getattr(exc, "response_data", None),
        }

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
    return str(value).capitalize()
