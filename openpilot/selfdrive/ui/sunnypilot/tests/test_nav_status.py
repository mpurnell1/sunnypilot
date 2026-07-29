"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.cereal import custom, log
from openpilot.common.params import Params

from openpilot.selfdrive.ui.sunnypilot import nav_status as nav_status_module
from openpilot.selfdrive.ui.sunnypilot.nav_status import (
  GPS_ACQUIRE_CONFIRM_SECONDS, GPS_LOST_HOLD_SECONDS, ROUTE_FAILURE_THRESHOLD, NavState, NavStatus,
)

NetworkType = log.DeviceState.NetworkType

STARTED_FRAME = 100


class MockSM(dict):
  def __init__(self):
    super().__init__()
    self.alive = {'navigationd': False}
    # navigationd publishes msg.valid straight from the localizer, and navigationd.valid once a route loads
    self.valid = {'navigationd': False}
    # fresher than started_frame: data received during this drive, not the previous one
    self.recv_frame = {'navigationd': STARTED_FRAME + 1}
    self['navigationd'] = custom.Navigationd.new_message()
    self.set_network(NetworkType.cell4G)

  def set(self, alive: bool, gps_valid: bool = False, route_valid: bool = False, failures: int = 0):
    self.alive['navigationd'] = alive
    self.valid['navigationd'] = gps_valid
    msg = custom.Navigationd.new_message()
    msg.valid = route_valid
    msg.routeFailures = failures
    self['navigationd'] = msg

  def set_network(self, network_type):
    msg = log.DeviceState.new_message()
    msg.networkType = network_type
    self['deviceState'] = msg


class TestNavStatus:
  def setup_method(self, method):
    self.params = Params()
    self.params.put("MapboxRoute", "", block=True)
    self.params.put("AllowNavigation", True, block=True)

    self.sm = MockSM()
    self.now = 1000.0
    # both are module/singleton state, so they are restored in teardown rather than left patched
    self._real_sm = nav_status_module.ui_state.sm
    self._real_monotonic = nav_status_module.monotonic
    self._real_started_frame = nav_status_module.ui_state.started_frame
    nav_status_module.ui_state.sm = self.sm
    nav_status_module.ui_state.started_frame = STARTED_FRAME
    nav_status_module.monotonic = lambda: self.now

    self.status = NavStatus()

  def teardown_method(self, method):
    nav_status_module.ui_state.sm = self._real_sm
    nav_status_module.ui_state.started_frame = self._real_started_frame
    nav_status_module.monotonic = self._real_monotonic

  def tick(self, seconds: float = 0.0):
    self.now += seconds
    # the destination is only re-read on a poll interval, so force it when the clock hasn't moved
    self.status._last_poll_time = 0.0
    self.status.update()

  def acquire_fix(self):
    self.tick()
    self.tick(GPS_ACQUIRE_CONFIRM_SECONDS)

  def set_destination(self, destination: str):
    self.params.put("MapboxRoute", destination, block=True)

  def test_offline_when_navd_is_not_publishing(self):
    self.sm.set(alive=False)
    self.tick()
    assert self.status.state == NavState.OFFLINE
    assert not self.status.gps_locked

  def test_no_destination(self):
    self.sm.set(alive=True, gps_valid=True)
    self.acquire_fix()
    assert self.status.state == NavState.NO_DESTINATION
    assert self.status.gps_locked

  def test_waiting_for_gps_with_a_destination(self):
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=False)
    self.tick()
    assert self.status.state == NavState.WAITING_FOR_GPS
    assert self.status.destination == "740 E Ventura Blvd"

  def test_computing_then_active(self):
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=True)
    self.acquire_fix()
    assert self.status.state == NavState.COMPUTING

    self.sm.set(alive=True, gps_valid=True, route_valid=True)
    self.tick()
    assert self.status.state == NavState.ACTIVE

  def test_gps_lock_is_held_across_a_dropped_sample(self):
    self.sm.set(alive=True, gps_valid=True)
    self.acquire_fix()
    assert self.status.gps_locked

    self.sm.set(alive=True, gps_valid=False)
    self.tick(GPS_LOST_HOLD_SECONDS / 2)
    assert self.status.gps_locked, "a single dropped localizer sample must not flicker the indicator"

    self.tick(GPS_LOST_HOLD_SECONDS)
    assert not self.status.gps_locked

  def test_stale_validity_does_not_survive_navd_dying(self):
    self.sm.set(alive=True, gps_valid=True)
    self.acquire_fix()
    assert self.status.gps_locked

    self.sm.alive['navigationd'] = False
    self.tick()
    assert not self.status.gps_locked
    assert self.status.state == NavState.OFFLINE

  def test_a_never_seen_fix_is_not_reported_as_locked(self):
    self.now = 0.5
    self.sm.set(alive=True, gps_valid=False)
    self.tick()
    assert not self.status.gps_locked

  def test_a_stale_lock_from_the_previous_drive_is_ignored(self):
    self.sm.set(alive=True, gps_valid=True)
    self.sm.recv_frame['navigationd'] = STARTED_FRAME - 1
    self.tick()
    assert not self.status.gps_locked
    assert self.status.state == NavState.OFFLINE

    # fresh data still has to earn the lock
    self.sm.recv_frame['navigationd'] = STARTED_FRAME + 1
    self.sm.set(alive=True, gps_valid=False)
    self.tick()
    assert not self.status.gps_locked

  def test_a_single_valid_sample_does_not_show_a_lock(self):
    self.sm.set(alive=True, gps_valid=True)
    self.tick()
    assert not self.status.gps_locked, "a fix must be sustained before it is believed"

    self.sm.set(alive=True, gps_valid=False)
    self.tick(0.1)
    assert not self.status.gps_locked

  def test_repeated_failures_are_reported_as_no_route(self):
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=True, failures=1)
    self.acquire_fix()
    assert self.status.state == NavState.COMPUTING, "one failure is still worth calling 'trying'"

    self.sm.set(alive=True, gps_valid=True, failures=ROUTE_FAILURE_THRESHOLD)
    self.tick()
    assert self.status.state == NavState.NO_ROUTE
    assert self.status.destination == "740 E Ventura Blvd"

  def test_no_route_names_the_offline_case(self):
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=True, failures=5)

    self.sm.set_network(NetworkType.none)
    self.tick()
    assert self.status.state == NavState.NO_ROUTE
    assert not self.status.online
    assert "offline" in self.status.route_text

    self.sm.set_network(NetworkType.cell4G)
    self.tick()
    assert self.status.online
    assert "offline" not in self.status.route_text

  def test_failures_outrank_a_dropped_fix(self):
    # navd keeps its last position, so it goes on requesting routes after the localizer drops;
    # reporting "waiting for GPS" there would hide the real failure
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=False, failures=5)
    self.tick()
    assert self.status.state == NavState.NO_ROUTE

  def test_a_loaded_route_outranks_stale_failures(self):
    self.set_destination("740 E Ventura Blvd")
    self.sm.set(alive=True, gps_valid=True, route_valid=True, failures=3)
    self.acquire_fix()
    assert self.status.state == NavState.ACTIVE

  def test_allow_navigation_is_tracked(self):
    self.params.put("AllowNavigation", False, block=True)
    self.sm.set(alive=True, gps_valid=True)
    self.tick()
    assert not self.status.allow_navigation

    self.params.put("AllowNavigation", True, block=True)
    self.tick()
    assert self.status.allow_navigation
