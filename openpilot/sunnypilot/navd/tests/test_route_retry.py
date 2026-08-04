"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import platform
import pytest
from concurrent.futures import Future

from openpilot.common.params import Params
from openpilot.sunnypilot.navd import navigationd as navigationd_module
from openpilot.sunnypilot.navd.constants import NAV_RETRY
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigationd import Navigationd

DESTINATION = "740 E Ventura Blvd"
ROUTE = {'steps': [{}], 'geometry': [{}]}


# a route request costs a Mapbox geocoding call and usually a directions call, and
# _update_params runs at the 3Hz loop rate, so failures must neither latch nor spin
class TestRouteRetry:
  is_darwin = platform.system() == "Darwin"

  @pytest.fixture(autouse=True)
  def setup(self, mocker):
    if self.is_darwin:
      mocker.patch('openpilot.cereal.messaging.SubMaster')
      mocker.patch('openpilot.cereal.messaging.PubMaster')

    Params().put("MapboxRoute", DESTINATION, block=True)

    self.now = 1000.0
    mocker.patch.object(navigationd_module, 'monotonic', lambda: self.now)

    self.nav = Navigationd()
    self.nav.last_position = Coordinate(latitude=34.23305, longitude=-119.17557)

    # set_destination reports whether a route is ready; get_current_route reads it back
    self.route_ready = False
    self.set_destination_calls = 0

    def fake_set_destination(postvars, *args, **kwargs):
      self.set_destination_calls += 1
      return postvars, self.route_ready

    mocker.patch.object(self.nav.mapbox, 'set_destination', side_effect=fake_set_destination)
    mocker.patch.object(self.nav.nav_instructions, 'clear_route_cache')
    mocker.patch.object(self.nav.nav_instructions, 'get_current_route',
                        side_effect=lambda: ROUTE if self.route_ready else None)

    # requests normally run on a worker thread; running them inline keeps the fake clock
    # authoritative over when a request happens and when its result lands
    def inline_submit(fn, *args, **kwargs):
      future = Future()
      future.set_result(fn(*args, **kwargs))
      return future

    mocker.patch.object(self.nav.executor, 'submit', side_effect=inline_submit)

  # the real loop runs at 3Hz, which is what makes an un-backed-off retry expensive
  def run_for(self, seconds: float) -> None:
    for _ in range(int(seconds * 3)):
      self.now += 1 / 3
      self.nav._update_params()

  def test_success_stores_the_route(self):
    self.route_ready = True
    self.run_for(1.0)
    assert self.nav.route == ROUTE
    assert self.nav.destination == DESTINATION
    assert self.set_destination_calls == 1, "a satisfied destination must not be requested again"

  def test_directions_failure_does_not_latch_the_destination(self):
    self.route_ready = False
    self.run_for(1.0)
    assert self.nav.destination is None
    assert self.nav.route is None

  def test_failures_are_retried_with_backoff_not_at_loop_rate(self):
    self.route_ready = False
    self.run_for(60.0)
    # 3Hz over 60s is 180 requests; the backoff schedule allows 10s + 20s + 40s
    assert self.set_destination_calls == 3, f"expected 3 spaced retries, got {self.set_destination_calls}"

  def test_backoff_is_capped(self):
    self.route_ready = False
    self.run_for(3600.0)
    delays = [min(NAV_RETRY.BASE_SECONDS * 2 ** n, NAV_RETRY.MAX_SECONDS) for n in range(50)]
    expected, total = 0, 0.0
    for d in delays:
      if total > 3600.0:
        break
      total += d
      expected += 1
    assert self.set_destination_calls == expected
    assert self.nav.failed_attempts == expected

  def test_recovery_after_a_failure_run(self):
    self.route_ready = False
    self.run_for(30.0)
    failed_calls = self.set_destination_calls
    assert failed_calls > 1

    self.route_ready = True
    self.run_for(120.0)
    assert self.nav.route == ROUTE
    assert self.nav.destination == DESTINATION
    assert self.set_destination_calls == failed_calls + 1, "should stop requesting once it succeeds"

  def test_a_new_destination_does_not_inherit_the_backoff(self):
    self.route_ready = False
    self.run_for(120.0)
    calls_before = self.set_destination_calls

    Params().put("MapboxRoute", "somewhere else", block=True)
    self.route_ready = True
    # the param is only re-read every 15 frames, so allow a few seconds
    self.run_for(6.0)
    assert self.set_destination_calls == calls_before + 1, "a fresh destination must be tried immediately"
    assert self.nav.destination == "somewhere else"

  def test_a_failed_reroute_keeps_the_existing_route(self):
    self.route_ready = True
    self.run_for(1.0)
    assert self.nav.route == ROUTE

    # off-route long enough to trigger a recompute, which then fails
    self.route_ready = False
    self.nav.recompute_allowed = True
    self.nav.reroute_counter = 10
    calls_before = self.set_destination_calls
    self.run_for(30.0)

    assert self.set_destination_calls > calls_before, "a reroute should have been attempted"
    assert self.nav.route == ROUTE, "a failed reroute must not discard the working route"
    assert self.nav.destination == DESTINATION

  def test_arrival_unlatches_the_destination(self):
    self.route_ready = True
    self.run_for(1.0)
    assert self.nav.destination == DESTINATION

    # arrival drives arrival_counter to 30
    self.nav.arrival_counter = 30
    self.run_for(1 / 3)
    assert self.nav.route is None
    assert self.nav.destination is None, "re-entering the same address must be able to start a new route"

  def test_clearing_the_destination_forgets_the_stored_route(self):
    self.route_ready = True
    self.run_for(1.0)
    assert self.nav.route == ROUTE

    Params().put("MapboxSettings", {"navData": {"route": {"steps": [{}]}}}, block=True)
    Params().put("MapboxRoute", "", block=True)
    # the empty value must be seen on two consecutive 5s polls before the route drops
    self.run_for(11.0)

    assert self.nav.route is None
    assert self.nav.destination is None
    assert Params().get("MapboxSettings") is None, "stored route must not survive a clear"

  def test_a_result_for_a_stale_destination_is_discarded(self):
    self.route_ready = True
    request = Future()
    request.set_result(({}, True))
    self.nav.route_request = request
    self.nav.attempted_destination = "the old destination"
    self.nav.frame = 0  # keep clear of the poll so new_destination stays as set
    self.nav.new_destination = DESTINATION

    self.nav._update_params()
    assert self.nav.route is None, "a route for a destination the driver replaced must not land"
    assert self.nav.failed_attempts == 0, "a discarded result is not a failure to back off from"

  def test_a_transient_empty_read_holds_the_route(self):
    self.route_ready = True
    self.run_for(1.0)
    assert self.nav.route == ROUTE

    # one poll sees the destination empty, then the value is back — a glitch, not a clear
    Params().put("MapboxRoute", "", block=True)
    self.run_for(5.0)
    Params().put("MapboxRoute", DESTINATION, block=True)
    self.run_for(6.0)

    assert self.nav.route == ROUTE, "a one-poll empty read must not kill the route"
    assert self.nav.destination == DESTINATION
    assert self.nav.empty_destination_reads == 0

  def test_arrival_forgets_the_stored_route(self):
    self.route_ready = True
    self.run_for(1.0)
    Params().put("MapboxSettings", {"navData": {"route": {"steps": [{}]}}}, block=True)

    self.nav.arrival_counter = 30
    self.run_for(1 / 3)
    assert self.nav.route is None
    assert Params().get("MapboxSettings") is None
