"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
from openpilot.selfdrive.ui.mici.widgets.button import BigParamControl


class TogglesLayoutMiciSP(TogglesLayoutMici):
  """The stock mici toggles plus the navigation master switch. One row is the whole
  on-device surface by design: turning it on starts destinationd, and every other
  navigation choice (destinations, HUD, audio, token) lives on the phone page that
  daemon serves, where a real keyboard and description text exist. Options that
  influence steering stay off the page entirely and are set over SSH, so consent
  for them happens in the car."""

  def __init__(self):
    super().__init__()
    self._nav_toggle = BigParamControl("navigation", "AllowNavigation")
    self._nav_toggle.set_value("set up on the phone page, port 5050")
    self._scroller.add_widget(self._nav_toggle)
    # external writes (athena, SSH) land in the row like every other toggle
    self._refresh_toggles = (*self._refresh_toggles, ("AllowNavigation", self._nav_toggle))
