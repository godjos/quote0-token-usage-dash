import io
import unittest

from PIL import Image

from render import H, W, render_image
from usage import (
    UsageProviderResult,
    UsageRow,
    _parse_kimi_payload,
    parse_usage_providers,
)


class ProviderConfigTests(unittest.TestCase):
    def test_default_providers(self):
        self.assertEqual(parse_usage_providers(""), ("claude", "openai"))

    def test_valid_two_providers(self):
        self.assertEqual(parse_usage_providers("claude,kimi"), ("claude", "kimi"))

    def test_rejects_duplicates(self):
        with self.assertRaises(ValueError):
            parse_usage_providers("kimi,kimi")

    def test_rejects_unknown_provider(self):
        with self.assertRaises(ValueError):
            parse_usage_providers("claude,gemini")

    def test_rejects_more_than_two(self):
        with self.assertRaises(ValueError):
            parse_usage_providers("claude,openai,kimi")


class KimiUsageParsingTests(unittest.TestCase):
    def test_parses_limit_windows(self):
        payload = {
            "usage": {"remaining": 80, "limit": 100},
            "limits": [
                {
                    "window": {"duration": 300, "timeUnit": "MINUTE"},
                    "detail": {"used": 25, "limit": 100, "reset_in": 3600},
                },
                {
                    "window": {"duration": 7, "timeUnit": "DAY"},
                    "detail": {
                        "remaining": 60,
                        "limit": 100,
                        "reset_at": "2026-06-03T00:00:00Z",
                    },
                },
            ],
        }

        usage = _parse_kimi_payload(payload)

        self.assertEqual(usage.provider_id, "kimi")
        self.assertEqual(usage.title, "Kimi Code")
        self.assertEqual([row.label for row in usage.rows], ["5h", "7d"])
        self.assertEqual([row.used_percent for row in usage.rows], [25.0, 40.0])
        self.assertIsNotNone(usage.rows[0].resets_at)
        self.assertIsNotNone(usage.rows[1].resets_at)

    def test_uses_summary_when_limits_missing(self):
        usage = _parse_kimi_payload({"usage": {"used": 30, "limit": 100}})

        self.assertEqual(len(usage.rows), 1)
        self.assertEqual(usage.rows[0].label, "7d")
        self.assertEqual(usage.rows[0].used_percent, 30.0)


class RenderTests(unittest.TestCase):
    def test_renders_two_provider_png(self):
        png = render_image(
            [
                UsageProviderResult(
                    provider_id="claude",
                    title="Claude",
                    rows=[UsageRow("5h", 40.0, reset_hint="2h")],
                ),
                UsageProviderResult(
                    provider_id="kimi",
                    title="Kimi Code",
                    rows=[UsageRow("7d", 70.0, reset_hint="1d")],
                ),
            ]
        )

        self.assertGreater(len(png), 0)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.size, (W, H))


if __name__ == "__main__":
    unittest.main()
