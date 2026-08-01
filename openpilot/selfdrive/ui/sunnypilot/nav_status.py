"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from enum import IntEnum
from time import monotonic

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr

NetworkType = log.DeviceState.NetworkType


class NavState(IntEnum):
  OFFLINE = 0          # navigationd isn't publishing: offroad, or the process is down
  NO_DESTINATION = 1   # nav is up, but nothing has been set
  WAITING_FOR_GPS = 2  # a destination is set, but the localizer has no fix yet
  COMPUTING = 3        # fix and destination, no route back from Mapbox yet
  NO_ROUTE = 4         # route requests keep failing, so it is not merely still trying
  ACTIVE = 5           # a route is loaded


# navd's retry backoff puts the second failure ~10s in
ROUTE_FAILURE_THRESHOLD = 2

# navigationd sets msg.valid from the instantaneous localizer fix but keeps its last position
# across short dropouts, so the display tolerates the same gaps the daemon does
GPS_LOST_HOLD_SECONDS = 2.0
GPS_ACQUIRE_CONFIRM_SECONDS = 1.0

DESTINATION_POLL_SECONDS = 1.0


# navigationd exposes the two preconditions separately: the message's own valid flag is the
# localizer fix, and navigationd.valid means a route is loaded. The onroad indicator and the
# navigation settings panel share this so they cannot disagree.
class NavStatus:
  def __init__(self):
    self._params = Params()
    self.destination: str = ""
    self.allow_navigation: bool = False
    self.show_gps_icon: bool = True
    self.show_route_icon: bool = True
    self.nav_hud_mode: int = 3
    self.lane_guidance: bool = False
    self.gps_locked: bool = False
    self.online: bool = False
    self.state: NavState = NavState.OFFLINE
    self._last_fix_time: float | None = None
    self._fix_since: float | None = None
    self._fix_confirmed: bool = False
    self._last_poll_time: float = 0.0

  # deliberately asymmetric: unbroken validity to acquire, a grace period to lose
  def _update_fix(self, now: float, valid: bool) -> None:
    if valid:
      if self._fix_since is None:
        self._fix_since = now
      self._last_fix_time = now
      if now - self._fix_since >= GPS_ACQUIRE_CONFIRM_SECONDS:
        self._fix_confirmed = True
    else:
      self._fix_since = None
      if self._last_fix_time is None or (now - self._last_fix_time) >= GPS_LOST_HOLD_SECONDS:
        self._fix_confirmed = False

  def update(self) -> None:
    now = monotonic()
    if now - self._last_poll_time >= DESTINATION_POLL_SECONDS:
      self._last_poll_time = now
      self.destination = self._params.get("MapboxRoute") or ""
      self.allow_navigation = self._params.get_bool("AllowNavigation")
      self.show_gps_icon = self._params.get_bool("NavShowGpsIcon")
      self.show_route_icon = self._params.get_bool("NavShowRouteIcon")
      self.nav_hud_mode = self._params.get("NavHudMode", return_default=True)
      self.lane_guidance = self._params.get_bool("NavLaneGuidance")

    sm = ui_state.sm
    # sm.valid holds its last received value indefinitely, and the conflated socket can hand
    # the UI a message from the previous ignition cycle, so both checks are load-bearing
    running = sm.alive["navigationd"] and sm.recv_frame["navigationd"] >= ui_state.started_frame
    self._update_fix(now, running and sm.valid["navigationd"])
    self.gps_locked = running and self._fix_confirmed
    self.online = sm["deviceState"].networkType != NetworkType.none

    if not running:
      self.state = NavState.OFFLINE
    elif not self.destination:
      self.state = NavState.NO_DESTINATION
    elif sm["navigationd"].valid:
      self.state = NavState.ACTIVE
    elif sm["navigationd"].routeFailures >= ROUTE_FAILURE_THRESHOLD:
      # ranked above the fix check: navd keeps requesting routes after the localizer drops
      self.state = NavState.NO_ROUTE
    elif not self.gps_locked:
      self.state = NavState.WAITING_FOR_GPS
    else:
      self.state = NavState.COMPUTING

  # NavHudMode is a bitmask presented as Off / Turns / ETA / Both in settings
  @property
  def show_turn_indicator(self) -> bool:
    return bool(self.nav_hud_mode & 1)

  @property
  def show_route_summary(self) -> bool:
    return bool(self.nav_hud_mode & 2)

  @property
  def gps_text(self) -> str:
    if self.state == NavState.OFFLINE:
      return tr("Offline")
    return tr("Locked") if self.gps_locked else tr("Waiting for fix...")

  # no connection is the common cause and the one the driver can act on, so it is named
  @property
  def no_route_text(self) -> str:
    return tr("No route - device offline") if not self.online else tr("Route request failed")

  @property
  def route_text(self) -> str:
    if self.state == NavState.NO_ROUTE:
      return self.no_route_text
    return {
      NavState.OFFLINE: tr("Navigation not running"),
      NavState.NO_DESTINATION: tr("No destination set"),
      NavState.WAITING_FOR_GPS: tr("Waiting for GPS fix"),
      NavState.COMPUTING: tr("Computing route..."),
      NavState.ACTIVE: tr("Active"),
    }[self.state]
