import json
import os
import sys
import unittest
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import CommandServer, Device, Signal  # noqa: E402
from ujin_client import UjinClient  # noqa: E402
from devices import REGISTRY  # noqa: E402
from devices.leak_sensor import LeakSensor  # noqa: E402
from devices.barrier import Barrier  # noqa: E402
from devices.thermostat import Thermostat  # noqa: E402
from devices.elevator import Elevator  # noqa: E402
from devices.leak_hub import LeakHub  # noqa: E402


class RecordingClient(UjinClient):
    def __init__(self):
        super().__init__(dry_run=True, logger=lambda msg: None)
        self.published = []

    def update_signals(self, serialnumber, model, batch):
        self.published.append({"serial": serialnumber, "batch": list(batch)})
        return {"error": 0}


def published_map(client):
    out = {}
    for entry in client.published:
        for item in entry["batch"]:
            out[item["signal"]] = item["value"]
    return out


class LeakTest(unittest.TestCase):
    def test_wet_and_self_test(self):
        c = RecordingClient()
        d = LeakSensor(serial="1", client=c)
        d.set_wet(True)
        d.step()
        self.assertEqual(published_map(c).get("leak"), 1)
        d.handle_recv({"recv": [{"command": "management", "id": "1",
                                 "self_test": 1}]})
        d.step()
        self.assertEqual(published_map(c).get("leak"), 0.001)  # wire 0->0.001 (мина №1)

    def test_battery_drains(self):
        c = RecordingClient()
        d = LeakSensor(serial="1", client=c, battery_drain=1.0)
        d.step()
        self.assertEqual(published_map(c)["battery"], 99.0)


class BarrierTest(unittest.TestCase):
    def _open_cycle(self):
        c = RecordingClient()
        d = Barrier(serial="2", client=c, default_hold=2)
        d.handle_recv({"recv": [{"command": "management", "id": "2",
                                 "open_cmd": 1}]})
        for _ in range(10):
            d.step()
        state = published_map(c)["state"]
        return c, d, state

    def test_auto_close_after_hold(self):
        _, d, state = self._open_cycle()
        self.assertEqual(state, 0.001)  # wire 0->0.001 (мина №1)
        self.assertEqual(d.signals["cycles"].value, 1)

    def test_obstacle_reversal(self):
        c = RecordingClient()
        d = Barrier(serial="2", client=c, default_hold=60)
        d.handle_recv({"recv": [{"command": "management", "id": "2",
                                 "open_cmd": 1}]})
        for _ in range(4):
            d.step()
        self.assertEqual(d.signals["state"].value, 2)
        d.handle_recv({"recv": [{"command": "management", "id": "2",
                                 "close_cmd": 1}]})
        d.step()
        self.assertEqual(d.signals["state"].value, 3)
        d.set_obstacle(True)
        d.step()
        self.assertEqual(d.signals["state"].value, 2)

    def test_jam_alarm(self):
        c = RecordingClient()
        d = Barrier(serial="2", client=c, default_hold=60, jam_limit=1)
        d.handle_recv({"recv": [{"command": "management", "id": "2",
                                 "open_cmd": 1}]})
        d.step()
        d.step()
        self.assertEqual(d.signals["alarm"].value, 1)


class ThermostatTest(unittest.TestCase):
    def test_heat_until_target(self):
        c = RecordingClient()
        d = Thermostat(serial="3", client=c, start_temp=18.0,
                       ambient_pull=0.0, step_per_tick=0.5)
        d.handle_recv({"recv": [{"command": "management", "id": "3",
                                 "target_temp": 23}]})
        for _ in range(12):
            d.step()
        self.assertGreaterEqual(d.signals["temperature"].value, 23.0)
        self.assertLessEqual(d.signals["temperature"].value, 23.6)
        self.assertEqual(d.signals["output"].value, 0)

    def test_off_mode_no_output(self):
        c = RecordingClient()
        d = Thermostat(serial="3", client=c, start_temp=10.0)
        d.handle_recv({"recv": [{"command": "management", "id": "3",
                                 "mode": 0}]})
        for _ in range(5):
            d.step()
        self.assertEqual(d.signals["output"].value, 0)

    def test_fault_stops_output(self):
        c = RecordingClient()
        d = Thermostat(serial="3", client=c, start_temp=10.0)
        d.handle_recv({"recv": [{"command": "management", "id": "3",
                                 "target_temp": 30, "mode": 1}]})
        d.step()
        self.assertEqual(d.signals["output"].value, 1)
        d.set_fault(True)
        d.step()
        self.assertEqual(d.signals["output"].value, 0)


class ElevatorTest(unittest.TestCase):
    def test_call_moves_and_emits_events(self):
        c = RecordingClient()
        d = Elevator(serial="4", client=c, floors=5, move_ticks=1)
        d.handle_recv({"recv": [{"command": "management", "id": "4",
                                 "call_requested": 3}]})
        for _ in range(3):
            d.step()
        self.assertEqual(d.signals["floor_current"].value, 3)
        self.assertEqual(d.signals["door"].value, 1)
        events = [i["value"] for e in c.published for i in e["batch"]
                  if i["signal"] == "floor_changed"]
        self.assertEqual(events, [2, 3])

    def test_emergency_stops_motion(self):
        c = RecordingClient()
        d = Elevator(serial="4", client=c, floors=5, move_ticks=2)
        d.handle_recv({"recv": [{"command": "management", "id": "4",
                                 "call_requested": 5}]})
        d.step()
        d.handle_recv({"recv": [{"command": "management", "id": "4",
                                 "emergency_btn": 1}]})
        frozen = d.signals["floor_current"].value
        for _ in range(5):
            d.step()
        self.assertEqual(d.signals["floor_current"].value, frozen)
        self.assertEqual(d.signals["emergency"].value, 1)

    def test_call_ignored_when_emergency(self):
        c = RecordingClient()
        d = Elevator(serial="4", client=c)
        d.handle_recv({"recv": [{"command": "management", "id": "4",
                                 "emergency_btn": 1}]})
        d.handle_recv({"recv": [{"command": "management", "id": "4",
                                 "call_requested": 2}]})
        self.assertIsNone(d._target)


class HubTest(unittest.TestCase):
    def test_internal_commutation_aggregates(self):
        c = RecordingClient()
        d = LeakHub(serial="5", client=c, sensors=3)
        d.sensor(2).set_wet(True)
        d.step()
        m = published_map(c)
        self.assertEqual(m["leak"], 1)
        self.assertEqual(m["leak_sensors"], "010")
        self.assertEqual(m["siren"], 1)
        d.handle_recv({"recv": [{"command": "management", "id": "5",
                                 "mute": 1}]})
        d.step()
        self.assertEqual(published_map(c)["siren"], 0.001)  # wire 0->0.001 (мина №1)


class ClientTest(unittest.TestCase):
    def test_dry_run_records_calls(self):
        c = UjinClient(dry_run=True, logger=lambda msg: None)
        c.create_update_device(model="emul_barrier", apartment_id=1)
        c.update_signals("777", "emul_barrier",
                         [{"signal": "state", "value": 1},
                          {"signal": "cycles", "value": 2}])
        paths = [call["path"] for call in c.calls]
        self.assertEqual(paths, [
            "/api/import_device/create-update-device/",
            "/api/import_device/group-update-device-signal/",
        ])
        self.assertEqual(c.calls[0]["params"]["model"], "emul_barrier")

    def test_single_signal_uses_single_endpoint(self):
        c = UjinClient(dry_run=True, logger=lambda msg: None)
        c.update_signals("777", "m", [{"signal": "leak", "value": 1}])
        self.assertEqual(c.calls[0]["path"],
                         "/api/import_device/update-device-signal/")


class CommandServerTest(unittest.TestCase):
    def test_http_command_roundtrip(self):
        d = Elevator(serial="9", floors=5, move_ticks=1, logger=lambda msg: None)
        srv = CommandServer(d, port=0)
        srv.start_thread()
        try:
            payload = json.dumps({"recv": [{"command": "management", "id": "9",
                                            "call_requested": 2}]})
            url = (f"http://127.0.0.1:{srv.port}/?data="
                   + urllib.parse.quote(payload))
            with urllib.request.urlopen(url, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(body["error"], 0)
            self.assertEqual(d._target, 2)
        finally:
            srv.stop()


class AnnounceTest(unittest.TestCase):
    def test_announce_publishes_only_publishable(self):
        c = RecordingClient()
        d = LeakSensor(serial="1", client=c)
        d.announce()
        names = {i["signal"] for e in c.published for i in e["batch"]}
        self.assertEqual(names,
                         {s.name for s in d.signals.values() if s.publishable})
        self.assertNotIn("self_test", names)


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.d = Barrier(serial="8", logger=lambda msg: None)
        self.srv = CommandServer(self.d, port=0)
        self.srv.start_thread()

    def tearDown(self):
        self.srv.stop()

    def _get(self, path):
        url = f"http://127.0.0.1:{self.srv.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.read().decode("utf-8")

    def test_dashboard_html_lists_signals(self):
        html = self._get("/")
        self.assertIn("<html", html)
        self.assertIn("open_cmd", html)
        self.assertIn("Состояние стрелы", html)

    def test_state_json(self):
        state = json.loads(self._get("/state"))
        self.assertEqual(state["state"], 0)
        self.assertEqual(state["hold_time"], 10)

    def test_command_form_sets_value(self):
        body = urllib.parse.urlencode({"signal": "hold_time", "value": "42"}).encode()
        url = f"http://127.0.0.1:{self.srv.port}/command"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type",
                       "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=5) as resp:
            answer = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(answer["error"], 0)
        self.assertEqual(self.d.signals["hold_time"].value, 42)


class AutoscriptsClientTest(unittest.TestCase):
    def test_scenario_methods_recorded(self):
        c = UjinClient(dry_run=True, logger=lambda msg: None)
        c.autoscripts_signals("ust-1-abc")
        c.autoscripts_edit("ust-1-abc", 0, "trigger",
                           condition="when:device-7:leak=1",
                           set_signal="device-7:siren:1:10")
        c.autoscripts_activate("ust-1-abc", 5)
        paths = [call["path"] for call in c.calls]
        self.assertEqual(paths, [
            "/api/autoscripts/signals/",
            "/api/autoscripts/edit/",
            "/api/autoscripts/activate/",
        ])
        edit = c.calls[1]["params"]
        self.assertEqual(edit["scenario_type"], "trigger")
        self.assertEqual(edit["set_signal"], "device-7:siren:1:10")


class RegistryTest(unittest.TestCase):
    def test_registry_models_unique_and_declared(self):
        models = [REGISTRY[k]("0").model for k in REGISTRY]
        self.assertEqual(len(models), len(set(models)))
        for k in REGISTRY:
            decl = REGISTRY[k]("0").declaration()
            self.assertTrue(any(s["name"] == "fw" for s in decl))


if __name__ == "__main__":
    unittest.main()


class TaskLoopTest(unittest.TestCase):
    """Приём управляющих сигналов: поллинг task + подтверждение (live_loop)."""

    INFO = {"error": 0, "data": {"signals": [
        {"name": "integrations_devices_open",
         "state": {"value": 0, "task": 1}},
        {"name": "integrations_devices_term",
         "state": {"value": 21.5}},
        {"name": "dt_create", "state": {"value": 1791895151}},
    ]}}

    def test_pending_tasks_extracts_only_pending(self):
        from live_loop import run_once
        got = UjinClient.pending_tasks(self.INFO)
        self.assertEqual(got, {"integrations_devices_open": 1})

    def test_run_once_executes_and_confirms(self):
        from live_loop import run_once

        class InfoClient(RecordingClient):
            def get_device_info(self, ust, sn):
                return TaskLoopTest.INFO

        c = InfoClient()
        applied = []
        done = run_once(c, "ust", "1", "ext", "parkcloud_device",
                        "integrations_devices", "integrations_devices_",
                        lambda s, v: applied.append((s, v)))
        self.assertEqual(done, ["integrations_devices_open"])
        self.assertEqual(applied, [("open", 1)])
        self.assertEqual(published_map(c), {"integrations_devices_open": 1})

    def test_dry_run_client_has_get_device_info(self):
        c = UjinClient(dry_run=True, logger=lambda msg: None)
        r = c.get_device_info("ust", "1")
        self.assertEqual(r["error"], 0)
        self.assertEqual(c.calls[-1]["path"],
                         "/api/bmsdevice/get-device-info/")
