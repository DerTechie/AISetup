"""Stdlib unittest cases for refresh.py pure functions (no network)."""
import unittest

from refresh import parse_openrouter_payload


class ParseOpenrouterPayload(unittest.TestCase):
    def test_extracts_prompt_and_completion_as_floats(self):
        payload = {"data": [{
            "id": "deepseek/deepseek-v4-flash",
            "pricing": {"prompt": "0.000000112", "completion": "0.000000224"},
        }]}
        out = parse_openrouter_payload(payload)
        self.assertEqual(
            out, {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        )

    def test_skips_model_missing_completion_price(self):
        payload = {"data": [{
            "id": "weird/half-priced",
            "pricing": {"prompt": "0.000001"},
        }]}
        self.assertEqual(parse_openrouter_payload(payload), {})

    def test_skips_model_missing_prompt_price(self):
        payload = {"data": [{
            "id": "weird/no-input-price",
            "pricing": {"completion": "0.000001"},
        }]}
        self.assertEqual(parse_openrouter_payload(payload), {})

    def test_skips_model_missing_id(self):
        payload = {"data": [{
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }]}
        self.assertEqual(parse_openrouter_payload(payload), {})

    def test_skips_model_missing_pricing_block(self):
        payload = {"data": [{"id": "no-pricing-at-all"}]}
        self.assertEqual(parse_openrouter_payload(payload), {})

    def test_skips_model_with_non_numeric_price(self):
        payload = {"data": [{
            "id": "broken/string-price",
            "pricing": {"prompt": "free", "completion": "0.00001"},
        }]}
        self.assertEqual(parse_openrouter_payload(payload), {})

    def test_zero_price_is_kept(self):
        payload = {"data": [{
            "id": "vendor/free-tier",
            "pricing": {"prompt": "0", "completion": "0"},
        }]}
        self.assertEqual(parse_openrouter_payload(payload), {"vendor/free-tier": (0.0, 0.0)})


if __name__ == "__main__":
    unittest.main()
