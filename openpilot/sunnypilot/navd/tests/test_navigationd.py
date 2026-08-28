"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import platform
import pytest

import openpilot.cereal.messaging as messaging

from openpilot.sunnypilot.navd.navigationd import OFF_ROUTE_DEBOUNCE_TICKS, Navigationd
from openpilot.sunnypilot.navd.helpers import Coordinate


class TestNavigationd:
  is_darwin = platform.system() == "Darwin"

  @pytest.fixture(autouse=True)
  def setup_method(self, mocker):
    if self.is_darwin:
      mocker.patch('openpilot.cereal.messaging.SubMaster')
      mocker.patch('openpilot.cereal.messaging.PubMaster')

  def test_update_params(self):
    nav = Navigationd()
    nav.last_position = None
    nav._update_params()
    assert nav.frame == -1
    nav.last_position = Coordinate(latitude=37.0, longitude=128.0)
    nav._update_params()
    assert nav.frame == 0  # frame only updates when last position is set

  def test_update_navigation_no_position(self):
    nav = Navigationd()
    nav.last_position = None
    banner, progress, nav_data = nav._update_navigation()
    assert banner == ''
    assert progress is None
    assert nav_data == {}

  def test_update_navigation(self):
    nav = Navigationd()
    nav.last_position = Coordinate(latitude=37.0, longitude=128.0)
    nav.route = {'580 Winchester dr, oxnard, CA': True}
    banner, progress, nav_data = nav._update_navigation()
    assert isinstance(banner, str)
    assert not progress  # no route was actually set
    assert isinstance(nav_data, dict)

  def test_final_step_blocks_reroute(self):
    nav = Navigationd()
    nav.last_position = Coordinate(latitude=37.0, longitude=128.0)
    nav.mapbox.set_destination = lambda *args, **kwargs: ({}, False)
    nav.frame = 0  # keep clear of the periodic param re-read so the fixture state holds
    nav.route = {'steps': []}
    nav.destination = "123 Main St"
    nav.new_destination = "123 Main St"
    nav.attempted_destination = "123 Main St"
    nav.recompute_allowed = True
    nav.reroute_counter = 10

    nav.final_step = True
    nav._update_params()
    assert not nav.allow_recompute, "final step must not trigger a reroute"

    nav.final_step = False
    nav._update_params()
    assert nav.allow_recompute

  def test_drop_route_clears_final_step(self):
    nav = Navigationd()
    nav.final_step = True
    nav._drop_route()
    assert not nav.final_step

  def test_route_state_debounce_ignores_a_blip(self):
    nav = Navigationd()
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS - 1):
      nav._update_route_standing(True, False, False)
      assert not nav.off_route, "a blip against the thresholds must not dim the display"
    nav._update_route_standing(True, False, False)
    assert nav.off_route

  def test_route_state_exits_via_the_counter_reset(self):
    nav = Navigationd()
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS):
      nav._update_route_standing(True, False, False)
    assert nav.off_route
    nav._update_route_standing(False, False, False)
    assert not nav.off_route
    assert nav.reroute_counter == 0

  def test_bearing_misalign_counts_toward_off_route(self):
    nav = Navigationd()
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS):
      nav._update_route_standing(False, True, False)
    assert nav.off_route

  def test_recompute_off_parks_at_off_route(self):
    # with recompute off there is no reroute to hope for, but the display must still say
    # the route is not being followed; the reroute trigger itself stays gated elsewhere
    nav = Navigationd()
    nav.recompute_allowed = False
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS):
      nav._update_route_standing(True, False, False)
    assert nav.off_route
    assert nav.reroute_counter == OFF_ROUTE_DEBOUNCE_TICKS

  def test_arrival_hold_reports_on_route(self):
    # the last meters to the flag routinely leave the mapped line, and that is not lost
    nav = Navigationd()
    for _ in range(OFF_ROUTE_DEBOUNCE_TICKS):
      nav._update_route_standing(True, False, False)
    assert nav.off_route
    nav._update_route_standing(True, False, True)
    assert not nav.off_route

  def test_route_state_publishes_with_rerouting_outranking(self):
    nav = Navigationd()
    assert nav._build_navigation_message('', None, {}, True).navigationd.routeState == 'onRoute'
    nav.off_route = True
    assert nav._build_navigation_message('', None, {}, True).navigationd.routeState == 'offRoute'
    nav.rerouting = True
    assert nav._build_navigation_message('', None, {}, True).navigationd.routeState == 'rerouting'

  def test_position_and_route_id_publish(self):
    nav = Navigationd()
    msg = nav._build_navigation_message('', None, {}, True)
    assert msg.navigationd.hasPosition is False
    assert msg.navigationd.routeId == 0

    nav.last_position = Coordinate(latitude=34.226, longitude=-119.032)
    nav.last_bearing = -90.0
    nav.route = {'route_id': 7}
    msg = nav._build_navigation_message('', None, {}, True)
    assert msg.navigationd.hasPosition is True
    assert msg.navigationd.positionLatitude == 34.226
    assert msg.navigationd.positionLongitude == -119.032
    assert msg.navigationd.positionBearingDeg == 270.0  # published normalized to [0, 360)
    assert msg.navigationd.routeId == 7

  def test_build_navigation_message(self):
    if self.is_darwin:
      nav = Navigationd()
      msg = nav._build_navigation_message('', None, {}, True)
      assert msg.navigationd.bannerInstructions == ''
      assert msg.navigationd.valid is False
    else:
      sm = messaging.SubMaster(['navigationd'])
      nav = Navigationd()
      msg = nav._build_navigation_message('', None, {}, True)

      nav.pm.send('navigationd', msg)
      sm.update()
      received_msg = sm['navigationd']

      assert received_msg.bannerInstructions == msg.navigationd.bannerInstructions
      assert received_msg.valid == msg.navigationd.valid
