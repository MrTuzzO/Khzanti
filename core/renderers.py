from rest_framework.renderers import JSONRenderer


class StandardRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = renderer_context.get("response")
        status_code = response.status_code

        if isinstance(data, dict) and "status" in data and "code" in data:
            return super().render(data, accepted_media_type, renderer_context)

        is_error = status_code >= 400

        wrapped = {
            "status": "error" if is_error else "success",
            "code": status_code,
            "message": _extract_message(data, status_code, is_error),
            "data": _extract_data(data, is_error),
        }

        return super().render(wrapped, accepted_media_type, renderer_context)


def _extract_message(data, status_code, is_error):
    if isinstance(data, dict) and "detail" in data:
        detail = data["detail"]
        return str(detail[0] if isinstance(detail, list) else detail).capitalize()

    if is_error:
        return _default_message(status_code)

    return "OK"


def _extract_data(data, is_error):
    if isinstance(data, dict) and "detail" in data:
        rest = {k: v for k, v in data.items() if k != "detail"}
        return rest or None

    return None if is_error and not isinstance(data, dict) else data


def _default_message(status_code):
    return {
        400: "Bad request.",
        401: "Unauthorized.",
        403: "Permission denied.",
        404: "Not found.",
        405: "Method not allowed.",
        429: "Too many requests.",
        500: "Internal server error.",
    }.get(status_code, "An error occurred.")
