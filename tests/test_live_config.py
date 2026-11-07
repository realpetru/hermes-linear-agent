import unittest

from linear_hermes.live_config import LiveConfig


class FakeCfg:
    def __init__(self, profile):
        self.agent_profile = profile


class LiveConfigTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.value = FakeCfg("appy")
        self.calls = 0

    def loader(self):
        self.calls += 1
        return self.value

    def build(self, ttl=5.0):
        return LiveConfig(self.loader, ttl=ttl, clock=lambda: self.now)

    def test_loads_once_within_ttl(self):
        live = self.build()
        live.get()
        live.get()
        self.assertEqual(self.calls, 1)

    def test_reloads_after_ttl(self):
        live = self.build(ttl=5)
        live.get()
        self.now = 6
        live.get()
        self.assertEqual(self.calls, 2)

    def test_picks_up_a_changed_profile(self):
        live = self.build(ttl=1)
        self.assertEqual(live.get().agent_profile, "appy")
        self.value = FakeCfg("cody")
        self.now = 2
        self.assertEqual(live.get().agent_profile, "cody")

    def test_change_is_logged(self):
        live = self.build(ttl=0)
        live.get()
        self.value = FakeCfg("cody")
        with self.assertLogs("linear_hermes.live_config", level="INFO") as logs:
            live.get()
        self.assertTrue(any("agent profile: appy -> cody" in m for m in logs.output))

    def test_zero_ttl_always_reloads(self):
        live = self.build(ttl=0)
        live.get()
        live.get()
        self.assertEqual(self.calls, 2)

    def test_loader_failure_keeps_last_good_value(self):
        live = self.build(ttl=0)
        first = live.get()

        def boom():
            raise RuntimeError("config vanished")

        live._loader = boom
        self.assertIs(live.get(), first)


if __name__ == "__main__":
    unittest.main()
