from rest_framework.renderers import JSONRenderer


class StandardRenderer(JSONRenderer):
    """
    Standard JSON renderer preserving native DRF data formatting.
    Empty responses (HTTP 204/205) return empty body bytes.
    """
    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = renderer_context.get("response") if renderer_context else None
        status_code = response.status_code if response else 200

        if status_code in (204, 205):
            return b""

        return super().render(data, accepted_media_type=accepted_media_type, renderer_context=renderer_context)
