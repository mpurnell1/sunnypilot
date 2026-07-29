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


# navd retries with backoff, so the second failure is ~10s in. Waiting for it avoids calling a
# single transient failure a dead loop, while not showing "computing" for a request that is not
# actually in flight.
ROUTE_FAILURE_THRESHOLD = 2


# navigationd sets msg.valid from the *instantaneous* localizer fix, so a single dropped
# sample would otherwise flicker the indicator. It keeps its last position across those
# gaps, so holding the display briefly matches what the daemon is actually doing.
GPS_LOST_HOLD_SECONDS = 2.0

# ...and the fix has to hold for this long before it is believed. The UI runs across ignition
# cycles on a conflated socket, so the first read after a car start can be the last message of
# the *previous* drive, which reported a lock. Without this, that stale sample latched the
# hold above and displayed "GPS locked" for two seconds before falling back to waiting.
GPS_ACQUIRE_CONFIRM_SECONDS = 1.0

DESTINATION_POLL_SECONDS = 1.0


class NavStatus:
  """Shared read-model for "is navigation actually working".

  navigationd exposes the two preconditions separately: the message's own valid flag is the
  localizer fix, and navigationd.valid means a usable route is loaded. Both the onroad
  indicator and the navigation settings panel read this so they can't disagree.
  """

  def __init__(self):
    self._params = Params()
    self.destination: str = ""
    self.allow_navigation: bool = False
    self.gps_locked: bool = False
    self.online: bool = False
    self.state: NavState = NavState.OFFLINE
    self._last_fix_time: float | None = None
    self._fix_since: float | None = None
    self._fix_confirmed: bool = False
    self._last_poll_time: float = 0.0

  def _update_fix(self, now: float, valid: bool) -> None:
    """Asymmetric: slow to trust a fix, quick to keep one.

    Acquiring needs GPS_ACQUIRE_CONFIRM_SECONDS of unbroken validity, so no single stale or
    spurious sample can ever show a lock. Once confirmed it survives GPS_LOST_HOLD_SECONDS
    without one, matching navd, which keeps using its last position across short dropouts.
    """
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

    sm = ui_state.sm
    # sm.valid holds the last received value forever, so it only means anything while navd is
    # alive *and* the message arrived during this drive. The socket is conflated and the UI
    # outlives ignition cycles, so without the frame check the first read can be last drive's.
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
      # checked before the fix, since navd keeps its last position and so keeps requesting
      # routes even after the localizer drops out
      self.state = NavState.NO_ROUTE
    elif not self.gps_locked:
      self.state = NavState.WAITING_FOR_GPS
    else:
      self.state = NavState.COMPUTING

  @property
  def gps_text(self) -> str:
    if self.state == NavState.OFFLINE:
      return tr("Offline")
    return tr("Locked") if self.gps_locked else tr("Waiting for fix...")

  @property
  def no_route_text(self) -> str:
    """Route requests are failing. No connection is by far the most common cause and the one
    the driver can act on, so it is called out rather than lumped in with a generic failure."""
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
