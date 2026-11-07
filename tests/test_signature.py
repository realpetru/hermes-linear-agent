import unittest

from linear_hermes.signature import compute_signature, verify_signature

SECRET = "s3cret"
BODY = b'{"type":"AgentSessionEvent","action":"created"}'


class SignatureTests(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(verify_signature(SECRET, BODY, compute_signature(SECRET, BODY)))

    def test_tampered_body_fails(self):
        sig = compute_signature(SECRET, BODY)
        self.assertFalse(verify_signature(SECRET, BODY + b" ", sig))

    def test_wrong_secret_fails(self):
        self.assertFalse(verify_signature(SECRET, BODY, compute_signature("other", BODY)))

    def test_sha256_prefix_tolerated(self):
        sig = "sha256=" + compute_signature(SECRET, BODY)
        self.assertTrue(verify_signature(SECRET, BODY, sig))

    def test_uppercase_hex_tolerated(self):
        self.assertTrue(verify_signature(SECRET, BODY, compute_signature(SECRET, BODY).upper()))

    def test_missing_header_fails(self):
        self.assertFalse(verify_signature(SECRET, BODY, None))
        self.assertFalse(verify_signature(SECRET, BODY, ""))

    def test_missing_secret_fails(self):
        self.assertFalse(verify_signature("", BODY, compute_signature(SECRET, BODY)))


if __name__ == "__main__":
    unittest.main()
