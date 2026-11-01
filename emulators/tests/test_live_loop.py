"""R-06: live_loop.run_once — один и тот же task исполняется один раз."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_loop import run_once  # noqa: E402


def info(task, value, dt=None, name="integrations_devices_rele"):
    sig = {"name": name, "task": task, "state": {"value": value}}
    if dt is not None:
        sig["signal_task_dt"] = dt
    return {"data": {"signals": [sig]}}


class FakeClient:
    def __init__(self):
        self.applied, self.confirmed = [], []

    def get_device_info(self, ust, sn):
        return self.info

    def update_signals(self, sn, model, rows):
        self.confirmed.append(rows)


class TestDedup(unittest.TestCase):
    def _run(self, infos):
        c = FakeClient()
        seen = {}
        for i in infos:
            c.info = i
            run_once(c, "ust", "1", "e", "parkcloud_device",
                     "integrations_devices", "integrations_devices_",
                     lambda s, v: c.applied.append((s, v)), seen)
        return c

    def test_same_task_once(self):
        i = info(1, 0, dt=111)
        c = self._run([i, i, i])
        self.assertEqual(c.applied, [("rele", 1)])
        self.assertEqual(len(c.confirmed), 1)

    def test_new_task_dt_executes_again(self):
        c = self._run([info(1, 0, dt=111), info(1, 0, dt=222)])
        self.assertEqual(c.applied, [("rele", 1), ("rele", 1)])

    def test_fallback_key_without_dt(self):
        # без task_dt ключ name=value: та же value — один раз, новая — снова
        c = self._run([info(1, 0), info(1, 0), info(0, 2)])
        self.assertEqual(c.applied, [("rele", 1), ("rele", 0)])

    def test_state_without_seen_untouched(self):
        # обратная совместимость: run_once без seen работает как раньше
        c = FakeClient()
        c.info = info(1, 0, dt=1)
        run_once(c, "ust", "1", "e", "parkcloud_device",
                 "integrations_devices", "integrations_devices_",
                 lambda s, v: c.applied.append(s))
        self.assertEqual(c.applied, ["rele"])


if __name__ == "__main__":
    unittest.main()
