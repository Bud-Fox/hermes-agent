import unittest
from unittest.mock import patch

from agent.banned_models import is_banned, banned_reason, load_banned_models
from agent.provider_health import preflight_provider_health, clear_health_cache, record_provider_health
from agent.error_classifier import classify_api_error


class Fake429(Exception):
    def __init__(self, msg, code=None, status=429):
        super().__init__(msg)
        self.status_code = status
        self.code = code
        self.message = msg


class TestBannedModels(unittest.TestCase):
    def test_banned(self):
        banned, reason = is_banned(None, "nvidia-foo")
        self.assertTrue(banned)
        self.assertIn("nvidia", reason.lower() or "")

    def test_not_banned(self):
        banned, _reason = is_banned(None, "gpt-4")
        self.assertFalse(banned)


class TestProviderHealth(unittest.TestCase):
    def setUp(self):
        clear_health_cache()

    def test_cache(self):
        record_provider_health("p", "m", healthy=False)
        self.assertFalse(preflight_provider_health("p", "m"))

    def test_probe(self):
        clear_health_cache()
        self.assertTrue(preflight_provider_health("p2", "m2", probe=lambda: True))


class TestNonRetryable429(unittest.TestCase):
    def test_exhaustion(self):
        err = Fake429("router_keys_exhausted", code="x")
        ce = classify_api_error(err, provider="x", model="m")
        self.assertFalse(ce.retryable)
        self.assertTrue(ce.should_fallback)

    def test_code_1113(self):
        err = Fake429("error code 1113", code="1113")
        ce = classify_api_error(err, provider="x", model="m")
        self.assertFalse(ce.retryable)
        self.assertTrue(ce.should_fallback)

    def test_normal_429(self):
        err = Fake429("rate limit exceeded, try again")
        ce = classify_api_error(err, provider="x", model="m")
        # normal remains retryable (default True) or at least not forced non-retryable?
        self.assertTrue(ce.retryable)


if __name__ == "__main__":
    unittest.main()
