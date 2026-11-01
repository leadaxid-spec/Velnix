import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ws_client  # noqa: E402
from devices.leak_sensor import LeakSensor  # noqa: E402
from devices.barrier import Barrier  # noqa: E402


class TestWsProtocol(unittest.TestCase):
    def test_build_auth(self):
        msg = json.loads(ws_client.build_auth("ust-1-x"))
        self.assertEqual(msg, {"token": "ust-1-x", "event": "authenticate",
                               "signals": True})

    def test_build_set_value(self):
        msg = json.loads(ws_client.build_set_value(42, "17515293610000463",
                                                   "rele1", 1))
        self.assertEqual(msg["event"], "setValue")
        dev = msg["devices"][0]
        self.assertEqual(dev["id"], 42)
        self.assertEqual(dev["sn"], 17515293610000463)
        self.assertEqual(dev["signals"], [{"name": "rele1", "value": "1"}])

    def test_parse_update(self):
        text = json.dumps({"v": "1.0", "event": "update",
                           "data": {"devices": [
                               {"sn": 1, "state": "online",
                                "signals": [{"name": "leak", "value": "1"}]}]}})
        kind, devices = ws_client.parse_message(text)
        self.assertEqual(kind, "update")
        self.assertEqual(devices[0]["sn"], 1)

    def test_parse_setvalue_echo(self):
        text = json.dumps({"event": "setValue",
                           "data": {"devices": [{"id": 9, "status": "failed"}]}})
        kind, devices = ws_client.parse_message(text)
        self.assertEqual(kind, "setValue")
        self.assertEqual(devices[0]["status"], "failed")


class TestWsHub(unittest.TestCase):
    def setUp(self):
        self.leak = LeakSensor(serial="17516841600000848")
        self.barrier = Barrier(serial="17515293610000463")
        self.hub = ws_client.WsDeviceHub([self.leak, self.barrier])

    def test_state_recorded(self):
        self.hub.handle("update", [{"sn": 17516841600000848, "state": "online"}])
        self.assertEqual(self.hub.states_seen["17516841600000848"], "online")

    def test_writable_signal_becomes_command(self):
        self.hub.handle("update", [{"sn": 17515293610000463,
                                    "signals": [{"name": "open_cmd", "value": "1"}]}])
        self.assertEqual(self.barrier.signals["state"].value, 1)  # OPENING

    def test_readonly_signal_not_command(self):
        self.hub.handle("update", [{"sn": 17516841600000848,
                                    "signals": [{"name": "battery", "value": "55"}]}])
        self.assertEqual(self.leak.remote_values.get("battery"), "55")

    def test_echo_by_uniq_id(self):
        self.hub.handle("setValue", [{"id": 777, "status": "failed"}])
        self.assertEqual(self.hub.echo[777], "failed")


if __name__ == "__main__":
    unittest.main()
