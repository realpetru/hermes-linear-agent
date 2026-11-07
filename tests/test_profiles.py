import os
import tempfile
import unittest

from linear_hermes.profiles import ProfileError, available_profiles, validate_profile


def make_home(names):
    home = tempfile.mkdtemp()
    for n in names:
        os.makedirs(os.path.join(home, n))
    return home


class ProfileTests(unittest.TestCase):
    def test_lists_disk_profiles_and_implicit_default(self):
        home = make_home(["cody", "appy"])
        self.assertEqual(available_profiles(home), ["appy", "cody", "default"])

    def test_missing_directory_still_offers_default(self):
        self.assertEqual(available_profiles("/nonexistent/nope"), ["default"])

    def test_empty_means_gateway_default(self):
        self.assertEqual(validate_profile("", make_home(["cody"])), "")

    def test_valid_profile_normalises_case(self):
        self.assertEqual(validate_profile("CODY", make_home(["cody"])), "cody")

    def test_invalid_profile_lists_available(self):
        with self.assertRaises(ProfileError) as ctx:
            validate_profile("nope", make_home(["cody", "appy"]))
        msg = str(ctx.exception)
        self.assertIn("Available profiles:", msg)
        self.assertIn("cody", msg)
        self.assertIn("default", msg)

    def test_invalid_profile_suggests_near_miss(self):
        with self.assertRaises(ProfileError) as ctx:
            validate_profile("coddy", make_home(["cody"]))
        self.assertIn("Did you mean", str(ctx.exception))
        self.assertIn("cody", str(ctx.exception))

    def test_error_never_mentions_a_traceback(self):
        with self.assertRaises(ProfileError) as ctx:
            validate_profile("zzz", make_home(["cody"]))
        self.assertNotIn("Traceback", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
