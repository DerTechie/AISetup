"""Stdlib unittest cases for the openrouter enrichment hook in seed_models.py."""
import unittest

from seed_models import enrich_openrouter_prices, OpenrouterPriceMissing


class EnrichOpenrouterPrices(unittest.TestCase):
    def test_adds_both_cost_fields_for_openrouter_model(self):
        params = {"model": "openrouter/deepseek/deepseek-v4-flash"}
        prices = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        out = enrich_openrouter_prices(params, prices)
        self.assertEqual(out["input_cost_per_token"], 0.000000112)
        self.assertEqual(out["output_cost_per_token"], 0.000000224)

    def test_leaves_non_openrouter_model_untouched(self):
        params = {"model": "ollama_chat/qwen3.6:27b"}
        out = enrich_openrouter_prices(params, {"anything": (1.0, 2.0)})
        self.assertNotIn("input_cost_per_token", out)
        self.assertNotIn("output_cost_per_token", out)

    def test_preserves_hand_set_input_cost(self):
        params = {
            "model": "openrouter/deepseek/deepseek-v4-flash",
            "input_cost_per_token": 0,
        }
        prices = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        out = enrich_openrouter_prices(params, prices)
        self.assertEqual(out["input_cost_per_token"], 0)
        # output still filled in
        self.assertEqual(out["output_cost_per_token"], 0.000000224)

    def test_raises_when_openrouter_does_not_know_model(self):
        params = {"model": "openrouter/vendor/never-existed"}
        with self.assertRaises(OpenrouterPriceMissing):
            enrich_openrouter_prices(params, {})


if __name__ == "__main__":
    unittest.main()
