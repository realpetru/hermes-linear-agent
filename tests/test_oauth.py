import os
import stat
import tempfile
import unittest

from linear_hermes import oauth


class OAuthTests(unittest.TestCase):
    def test_authorize_url_carries_actor_and_scopes(self):
        url = oauth.authorize_url("cid", "https://x/oauth/callback", state="s1")
        self.assertIn("actor=app", url)
        for scope in oauth.REQUIRED_SCOPES:
            self.assertIn(scope.replace(":", "%3A"), url)
        self.assertIn("state=s1", url)

    def test_parse_token_response_records_expiry(self):
        rec = oauth.parse_token_response(
            {"access_token": "a", "refresh_token": "r", "expires_in": 3600}, now=1000
        )
        self.assertEqual(rec["access_token"], "a")
        self.assertEqual(rec["expires_at"], 4600)

    def test_parse_requires_access_token(self):
        with self.assertRaises(oauth.OAuthError):
            oauth.parse_token_response({"nope": 1})

    def test_needs_refresh_skew(self):
        rec = {"expires_at": 1000}
        self.assertFalse(oauth.needs_refresh(rec, now=600))
        self.assertTrue(oauth.needs_refresh(rec, now=800))
        self.assertFalse(oauth.needs_refresh({}, now=9e9))

    def test_save_tokens_is_0600(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "tok.json")
        oauth.save_tokens(path, {"access_token": "a"})
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(oauth.load_tokens(path)["access_token"], "a")

    def test_exchange_code_posts_form(self):
        seen = {}

        def transport(url, headers, payload, timeout):
            seen["url"] = url
            seen["body"] = payload.decode()
            return 200, {"access_token": "a", "refresh_token": "r", "expires_in": 10}

        rec = oauth.exchange_code("cid", "sec", "thecode", "https://x/cb", transport)
        self.assertEqual(rec["access_token"], "a")
        self.assertIn("grant_type=authorization_code", seen["body"])
        self.assertIn("code=thecode", seen["body"])

    def test_refresh_uses_refresh_token_grant(self):
        def transport(url, headers, payload, timeout):
            self.assertIn("grant_type=refresh_token", payload.decode())
            return 200, {"access_token": "new", "refresh_token": "r2", "expires_in": 10}

        rec = oauth.refresh("cid", "sec", "r1", transport)
        self.assertEqual(rec["access_token"], "new")

    def test_token_provider_refreshes_when_stale(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "tok.json")
        oauth.save_tokens(path, {"access_token": "old", "refresh_token": "r",
                                 "expires_at": 1})

        def transport(url, headers, payload, timeout):
            return 200, {"access_token": "fresh", "refresh_token": "r2", "expires_in": 3600}

        tp = oauth.TokenProvider("cid", "sec", path, transport)
        self.assertEqual(tp.token(), "fresh")
        self.assertEqual(oauth.load_tokens(path)["access_token"], "fresh")

    def test_token_provider_without_tokens_raises(self):
        with self.assertRaises(oauth.OAuthError):
            oauth.TokenProvider("cid", "sec", "").token()


if __name__ == "__main__":
    unittest.main()
