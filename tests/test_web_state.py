"""Slot cards distinguish an effective mirror from two different outbounds."""
import copy
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "web-src/data/opt/etc/pivas-web/www/cgi-bin/state.sh"


class WebStateTests(unittest.TestCase):
    def evaluate(self, first, second):
        document = {"outbounds": [dict(first, tag="slot-1"), dict(second, tag="slot-2")]}
        from test_vless_url import v
        self.assertIn('vless_url.py state-shell', STATE.read_text())
        return v.state_shell(document)

    def outbound(self, address="first.example"):
        return {"protocol": "vless", "settings": {"vnext": [{"address": address, "port": 443,
                "users": [{"id": "00000000-0000-0000-0000-000000000001"}]}]},
                "streamSettings": {"network": "tcp", "realitySettings": {}}}

    def test_identical_configured_outbounds_are_marked_same(self):
        link = self.outbound()
        self.assertIn("slot2_same=true", self.evaluate(link, copy.deepcopy(link)))

    def test_different_outbounds_are_independent(self):
        self.assertIn("slot2_same=false", self.evaluate(self.outbound(), self.outbound("second.example")))

    def test_identical_placeholders_are_not_reported_as_links(self):
        placeholder = self.outbound("@SLOT1_ADDRESS")
        self.assertIn("slot2_same=false", self.evaluate(placeholder, copy.deepcopy(placeholder)))


if __name__ == "__main__":
    unittest.main()
