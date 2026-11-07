import os
import tempfile
import unittest

from linear_hermes.dedupe import DeliveryStore


class DedupeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = DeliveryStore(os.path.join(self.tmp, "s.sqlite3"), max_concurrency=2)

    def test_claim_once(self):
        self.assertTrue(self.store.claim("delivery:abc"))
        self.assertFalse(self.store.claim("delivery:abc"))

    def test_distinct_keys_both_claim(self):
        self.assertTrue(self.store.claim("delivery:a"))
        self.assertTrue(self.store.claim("delivery:b"))

    def test_concurrency_cap(self):
        self.assertTrue(self.store.try_acquire_slot("s1"))
        self.assertTrue(self.store.try_acquire_slot("s2"))
        self.assertFalse(self.store.try_acquire_slot("s3"))
        self.store.release_slot("s1")
        self.assertTrue(self.store.try_acquire_slot("s3"))

    def test_same_session_slot_is_idempotent(self):
        self.assertTrue(self.store.try_acquire_slot("s1"))
        self.assertTrue(self.store.try_acquire_slot("s1"))
        self.assertEqual(self.store.active_count(), 1)


if __name__ == "__main__":
    unittest.main()
