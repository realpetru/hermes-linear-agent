import os
import unittest

import isolation

from linear_hermes import exchange


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        # `exchange` reads through read_setting, which falls back to the credentials file:
        # without this, a `.env` in the checkout supplies the credentials this test removes.
        isolation.apply(self)

    def test_extract_bare_code(self):
        self.assertEqual(exchange.extract_code("abc123"), "abc123")

    def test_extract_from_redirect_url(self):
        url = "http://localhost:8650/oauth/callback?code=xyz&state=s"
        self.assertEqual(exchange.extract_code(url), "xyz")

    def test_extract_strips_whitespace(self):
        self.assertEqual(exchange.extract_code("  abc  "), "abc")

    def test_no_args_is_usage_error(self):
        self.assertEqual(exchange.main([]), 2)

    def test_missing_credentials_is_usage_error(self):
        old = dict(os.environ)
        try:
            os.environ.pop("LINEAR_CLIENT_ID", None)
            os.environ.pop("LINEAR_CLIENT_SECRET", None)
            self.assertEqual(exchange.main(["somecode"]), 2)
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
