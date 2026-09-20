"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.selfdrive.ui.mici.layouts.settings.toggles import TogglesLayoutMici
from openpilot.selfdrive.ui.mici.widgets.button import BigMultiParamToggle, BigParamControl


class TogglesLayoutMiciSP(TogglesLayoutMici):
  """The stock mici toggles plus navigation: the master switch that starts
  destinationd, and the choices that influence steering, kept on the device so
  consent for them happens in the car. Every other navigation choice (destinations,
  HUD, audio, token) lives on the phone page that daemon serves, where a real
  keyboard and description text exist."""

  def __init__(self):
    super().__init__()
    self._nav_toggle = BigParamControl("navigation", "AllowNavigation")
    self._nav_toggle.set_value("set up from the sunnynav app, or the device page on port 5050")
    self._desires_toggle = BigParamControl("navigation desires", "NavDesiresAllowed")
    self._desires_toggle.set_value("steer through a route turn once you signal for it")
    self._lane_toggle = BigParamControl("show lanes", "NavLaneGuidance")
    self._lane_toggle.set_value("lanes for the next maneuver on the turn card")
    self._lane_timer = BigMultiParamToggle("route lane change delay", "NavLaneChangeTimer", ["off", "nudgeless", "0.5 s", "1 s", "2 s", "3 s"])
    self._scroller.add_widgets([self._nav_toggle, self._desires_toggle, self._lane_toggle, self._lane_timer])
    # external writes (athena, the phone page, SSH) land in the rows like every other toggle
    self._refresh_toggles = (*self._refresh_toggles, ("AllowNavigation", self._nav_toggle),
                             ("NavDesiresAllowed", self._desires_toggle), ("NavLaneGuidance", self._lane_toggle))

  def _update_toggles(self):
    super()._update_toggles()
    self._lane_timer._load_value()
