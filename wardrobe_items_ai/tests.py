from unittest.mock import MagicMock, patch
from django.test import TestCase

from .models import ItemAnalysis, JobStatus
from .serializers import ItemAnalysisSerializer
from .services import _friendly_error_message, _parse_json_response


class ItemAnalysisUnitTests(TestCase):
    def test_friendly_error_message_mapping(self):
        self.assertIn("unavailable", _friendly_error_message("Billing credit exhausted"))
        self.assertIn("safety policies", _friendly_error_message("NSFW content detected"))
        self.assertIn("timed out", _friendly_error_message("Request timeout after 30s"))
        self.assertIn("Failed to analyze", _friendly_error_message("Random unhandled crash"))

    def test_parse_json_response(self):
        json_str = '{"matches_category": true, "color": "Navy Blue", "description": "Classic blazer"}'
        parsed = _parse_json_response(json_str)
        self.assertTrue(parsed.get("matches_category"))
        self.assertEqual(parsed.get("color"), "Navy Blue")
        self.assertEqual(parsed.get("description"), "Classic blazer")

        embedded_str = 'Here is the result:\n```json\n{"matches_category": false, "color": "Red", "description": "High heels"}\n```'
        parsed_embedded = _parse_json_response(embedded_str)
        self.assertFalse(parsed_embedded.get("matches_category"))
        self.assertEqual(parsed_embedded.get("color"), "Red")

    def test_serializer_excludes_internal_fields(self):
        fields = ItemAnalysisSerializer.Meta.fields
        self.assertNotIn("internal_error_detail", fields)
        self.assertNotIn("fal_request_id_bg_removal", fields)
        self.assertNotIn("fal_request_id_vision", fields)
        self.assertIn("color", fields)
        self.assertIn("description", fields)
        self.assertIn("error_message", fields)
