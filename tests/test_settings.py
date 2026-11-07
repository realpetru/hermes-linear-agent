import os
import stat
import tempfile
import unittest

from linear_hermes import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("LINEAR_AGENT_SETTINGS_DIR")
        os.environ["LINEAR_AGENT_SETTINGS_DIR"] = self.tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("LINEAR_AGENT_SETTINGS_DIR", None)
        else:
            os.environ["LINEAR_AGENT_SETTINGS_DIR"] = self._old

    def test_env_override_controls_location(self):
        self.assertEqual(settings.settings_dir(), self.tmp)

    def test_write_then_read_roundtrip(self):
        settings.set_stored_profile("cody")
        self.assertEqual(settings.stored_profile(), "cody")

    def test_file_is_0600(self):
        path = settings.set_stored_profile("cody")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(settings.read_settings(), {})
        self.assertEqual(settings.stored_profile(), "")

    def test_corrupt_file_reads_as_empty(self):
        path = os.path.join(self.tmp, settings.FILENAME)
        with open(path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(settings.read_settings(), {})

    def test_set_normalises_and_clears(self):
        settings.set_stored_profile(" CODY ")
        self.assertEqual(settings.stored_profile(), "cody")
        settings.set_stored_profile("")
        self.assertEqual(settings.stored_profile(), "")

    def test_env_shadow_detection(self):
        os.environ.pop("LINEAR_AGENT_PROFILE", None)
        self.assertFalse(settings.env_shadows_profile())
        os.environ["LINEAR_AGENT_PROFILE"] = "cody"
        try:
            self.assertTrue(settings.env_shadows_profile())
        finally:
            os.environ.pop("LINEAR_AGENT_PROFILE", None)


if __name__ == "__main__":
    unittest.main()
