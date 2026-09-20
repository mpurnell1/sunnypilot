"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.sunnylink.tools.generate_settings_schema import generate_schema
from openpilot.sunnypilot.sunnylink.utils import DEVICE_PAGE_PLACEHOLDER, device_page_hint, fill_device_page


def _description(schema, key):
  for panel in schema["panels"]:
    for section in panel.get("sections", []):
      for item in section.get("items", []):
        if item["key"] == key:
          return item["description"]
  raise KeyError(key)


class TestDevicePageHint:
  def test_wifi_lease_names_both_addresses(self):
    hint = device_page_hint("192.168.1.42")
    assert hint == "http://192.168.1.42:5050 on this wifi, or http://192.168.43.1:5050 with the device's Wifi Tethering on"

  def test_tethering_lease_is_the_tethering_address_alone(self):
    assert device_page_hint("192.168.43.1") == "http://192.168.43.1:5050 with the device's Wifi Tethering on"

  def test_no_lease_falls_back_to_tethering(self):
    assert device_page_hint("") == "http://192.168.43.1:5050 with the device's Wifi Tethering on"

  def test_allow_navigation_carries_the_placeholder_until_served(self):
    assert DEVICE_PAGE_PLACEHOLDER in _description(generate_schema(), "AllowNavigation")

  def test_served_schema_names_the_page(self):
    schema = fill_device_page(generate_schema(), device_page_hint("10.0.0.7"))
    description = _description(schema, "AllowNavigation")
    assert DEVICE_PAGE_PLACEHOLDER not in description
    assert "http://10.0.0.7:5050 on this wifi" in description
