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


from refresh import tick


class _UpdaterSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, model_id, input_per_token, output_per_token):
        self.calls.append((model_id, input_per_token, output_per_token))


def _route(name, model, input_cost=None, output_cost=None, route_id=None):
    return {
        "id": route_id or f"id-{name}",
        "name": name,
        "model": model,
        "input": input_cost,
        "output": output_cost,
    }


class Tick(unittest.TestCase):
    def test_skips_non_openrouter_routes(self):
        spy = _UpdaterSpy()
        routes = [_route("private", "ollama_chat/qwen3.6:27b")]
        checked, updated = tick({}, routes, updater=spy)
        self.assertEqual((checked, updated), (0, 0))
        self.assertEqual(spy.calls, [])

    def test_no_update_when_prices_match(self):
        spy = _UpdaterSpy()
        routes = [_route("main", "openrouter/deepseek/deepseek-v4-flash",
                         input_cost=0.000000112, output_cost=0.000000224)]
        live = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        checked, updated = tick(live, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 0))
        self.assertEqual(spy.calls, [])

    def test_update_when_input_or_output_changes(self):
        spy = _UpdaterSpy()
        routes = [_route("deep", "openrouter/google/gemini-3.1-pro-preview",
                         input_cost=0.0000019, output_cost=0.0000119, route_id="ROUTE-DEEP")]
        live = {"google/gemini-3.1-pro-preview": (0.000002, 0.000012)}
        checked, updated = tick(live, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 1))
        self.assertEqual(spy.calls, [("ROUTE-DEEP", 0.000002, 0.000012)])

    def test_update_when_current_prices_are_none(self):
        """Bootstrap case: route was seeded before prices were ever set."""
        spy = _UpdaterSpy()
        routes = [_route("main", "openrouter/deepseek/deepseek-v4-flash",
                         input_cost=None, output_cost=None, route_id="ROUTE-MAIN")]
        live = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        checked, updated = tick(live, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 1))
        self.assertEqual(spy.calls, [("ROUTE-MAIN", 0.000000112, 0.000000224)])

    def test_unknown_model_is_skipped_not_zeroed(self):
        """Fail-open: missing from live map => keep last-known price."""
        spy = _UpdaterSpy()
        routes = [_route("ghost", "openrouter/vendor/dropped-model",
                         input_cost=0.00001, output_cost=0.00002)]
        checked, updated = tick({}, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 0))
        self.assertEqual(spy.calls, [])

    def test_counts_only_openrouter_routes_as_checked(self):
        spy = _UpdaterSpy()
        routes = [
            _route("private", "ollama_chat/qwen3.6:27b"),
            _route("main", "openrouter/deepseek/deepseek-v4-flash",
                   input_cost=0.000000112, output_cost=0.000000224),
        ]
        live = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        checked, updated = tick(live, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 0))


if __name__ == "__main__":
    unittest.main()
