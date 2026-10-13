import unittest

from .structured_json import build_validator, parse_validated_object

SCHEMA = (
    '{"type":"object","properties":{"answer":{"type":"string"}},'
    '"required":["answer"],"additionalProperties":false}'
)


class StructuredJsonTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.validator = build_validator(SCHEMA)

    def test_non_json_prefix_is_classified_without_echoing_response(self):
        response = "PRIVATE_RESPONSE_TEXT that is not JSON"

        with self.assertRaises(RuntimeError) as raised:
            parse_validated_object(response, self.validator, "Pi CLI")

        message = str(raised.exception)
        self.assertIn("format=non_json_prefix", message)
        self.assertIn("parser=Expecting value", message)
        self.assertNotIn(response, message)

    def test_markdown_fence_is_reported_without_echoing_json(self):
        response = '```json\n{"answer":"private answer"}\n```'

        with self.assertRaises(RuntimeError) as raised:
            parse_validated_object(response, self.validator, "Pi CLI")

        message = str(raised.exception)
        self.assertIn("format=markdown_fence", message)
        self.assertNotIn("private answer", message)

    def test_malformed_object_reports_parser_location_only(self):
        response = '{"answer":'

        with self.assertRaises(RuntimeError) as raised:
            parse_validated_object(response, self.validator, "Pi CLI")

        message = str(raised.exception)
        self.assertIn("format=malformed_object", message)
        self.assertIn("line=1", message)
        self.assertIn("column=11", message)
        self.assertNotIn(response, message)

    def test_valid_non_object_reports_only_json_root_type(self):
        response = '["private answer"]'

        with self.assertRaises(TypeError) as raised:
            parse_validated_object(response, self.validator, "Pi CLI")

        self.assertIn("output_type=list", str(raised.exception))
        self.assertNotIn("private answer", str(raised.exception))

    def test_schema_failure_reports_constraint_without_instance_values(self):
        response = '{"wrong":"private answer"}'

        with self.assertRaises(RuntimeError) as raised:
            parse_validated_object(response, self.validator, "Pi CLI")

        message = str(raised.exception)
        self.assertIn("constraint=required", message)
        self.assertNotIn("private answer", message)


if __name__ == "__main__":
    unittest.main()
