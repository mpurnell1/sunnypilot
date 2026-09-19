"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
from openpilot.selfdrive.ui.mici.widgets.button import BigMultiParamToggle, BigParamControl


class TogglesLayoutMiciSP(TogglesLayoutMici):
  """The stock mici toggles plus navigation: the master switch that starts
  destinationd, and the two choices that influence steering, kept on the device so
  consent for them happens in the car. Every other navigation choice (destinations,
  HUD, audio, token) lives on the phone page that daemon serves, where a real
  keyboard and description text exist."""

  def __init__(self):
    super().__init__()
    self._nav_toggle = BigParamControl("navigation", "AllowNavigation")
    self._nav_toggle.set_value("set up on the phone page, port 5050")
    self._desires_toggle = BigParamControl("navigation desires", "NavDesiresAllowed")
    self._desires_toggle.set_value("steer through a route turn once you signal for it")
    self._lane_toggle = BigMultiParamToggle("lane guidance", "NavLaneGuidance", ["off", "display", "assist"])
    self._scroller.add_widgets([self._nav_toggle, self._desires_toggle, self._lane_toggle])
    # external writes (athena, the phone page, SSH) land in the rows like every other toggle
    self._refresh_toggles = (*self._refresh_toggles, ("AllowNavigation", self._nav_toggle),
                             ("NavDesiresAllowed", self._desires_toggle))

  def _update_toggles(self):
    super()._update_toggles()
    self._lane_toggle._load_value()
