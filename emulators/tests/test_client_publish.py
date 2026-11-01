import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ujin_client import UjinClient  # noqa: E402


def _client():
    logs = []
    return UjinClient(dry_run=True, logger=logs.append), logs


class TestPublishGroup(unittest.TestCase):
    def test_two_phase_order(self):
        cli, _ = _client()
        short = [{"sn": "e1", "s": "leak", "v": 0.001,
                  "c": "integrations_devices"}]
        full = [{"sn": "e1", "s": "integrations_devices_leak", "v": 1,
                 "c": "integrations_devices"}]
        cli.publish_group(full, new_short=short)
        posts = [c for c in cli.calls if "group-update" in c["path"]]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["payload"]["data"][0]["s"], "leak")
        self.assertEqual(posts[1]["payload"]["data"][0]["s"],
                         "integrations_devices_leak")

    def test_no_new_signals_single_call(self):
        cli, _ = _client()
        cli.publish_group([{"sn": "e1", "s": "integrations_devices_leak",
                            "v": 1, "c": "integrations_devices"}])
        posts = [c for c in cli.calls if "group-update" in c["path"]]
        self.assertEqual(len(posts), 1)

    def test_class_field_present(self):
        cli, _ = _client()
        cli.publish_group([{"sn": "e1", "s": "x", "v": 1,
                            "c": "integrations_devices"}])
        self.assertEqual(
            cli.calls[0]["payload"]["data"][0]["c"], "integrations_devices")


class TestUaeAuthMode(unittest.TestCase):
    """uae (31.10): import_device под ust резидента + apartment_id в QUERY
    (гейт verif_crm_user_apartment); dev-token не используем — дыра п.20."""

    def _uae(self):
        return UjinClient("https://uae.example", device_token="", dry_run=True,
                          logger=lambda m: None, cls="integrations_devices",
                          signal_prefix="integrations_devices_",
                          ust="ust-1-abc", apartment_id="2743")

    def test_preprod_query_unchanged(self):
        cli, _ = _client()
        self.assertTrue(cli._auth_qs("e1").startswith("device-token="))
        self.assertNotIn("apartment_id", cli._auth_qs("e1"))

    def test_uae_query_carries_ust_apt_sn(self):
        qs = self._uae()._auth_qs("e1")
        self.assertIn("token=ust-1-abc", qs)
        self.assertIn("apartment_id=2743", qs)
        self.assertIn("serialnumber=e1", qs)

    def test_uae_create_auth_not_in_body(self):
        cli = self._uae()
        cli.create_update_device("parkcloud_device", title="t",
                                 apartment_id=2743, device_ext_id="e1",
                                 serialnumber="e1")
        p = cli.calls[-1]["params"]
        self.assertNotIn("apartment_id", p)  # тело гейт не читает
        self.assertNotIn("device-token", p)
        self.assertEqual(p["serialnumber"], "e1")

    def test_uae_publish_rows_keep_ext_and_class(self):
        cli = self._uae()
        cli.publish_group([{"sn": "e1", "s": "integrations_devices_x",
                            "v": 1.001, "c": "integrations_devices"}])
        row = cli.calls[-1]["payload"]["data"][0]
        self.assertEqual(row["sn"], "e1")
        self.assertEqual(row["c"], "integrations_devices")


if __name__ == "__main__":
    unittest.main()
