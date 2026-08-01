"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from math import degrees
from numpy import interp
from time import monotonic

import openpilot.cereal.messaging as messaging
from openpilot.cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

from openpilot.sunnypilot.navd.constants import NAV_CV, NAV_RETRY
from openpilot.sunnypilot.navd.helpers import Coordinate, parse_banner_instructions
from openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration import MapboxIntegration
from openpilot.sunnypilot.navd.navigation_helpers.nav_instructions import NavigationInstructions


class Navigationd:
  def __init__(self):
    self.params = Params()
    self.mapbox = MapboxIntegration()
    self.nav_instructions = NavigationInstructions()

    self.sm = messaging.SubMaster(['carState', 'liveLocationKalman'])
    self.pm = messaging.PubMaster(['navigationd'])
    self.rk = Ratekeeper(3) # 3 Hz

    self.route = None
    self.destination: str | None = None
    self.new_destination: str = ''

    self.allow_navigation: bool = False
    self.recompute_allowed: bool = False
    self.allow_recompute: bool = False
    self.reroute_counter: int = 0
    self.cancel_route_counter: int = 0
    self.attempted_destination: str | None = None
    self.failed_attempts: int = 0
    self.next_attempt_time: float = 0.0
    self.final_step: bool = False

    self.frame: int = -1
    self.last_position: Coordinate | None = None
    self.last_bearing: float | None = None
    self.valid: bool = False

  # MapboxSettings outlives the in-memory route, so a route left there can be reloaded later
  def _drop_route(self) -> None:
    self.params.remove("MapboxSettings")
    self.nav_instructions.clear_route_cache()
    self.route = None
    self.destination = None
    self.cancel_route_counter = 0
    self.reroute_counter = 0
    self.final_step = False

  def _reset_retry(self) -> None:
    self.failed_attempts = 0
    self.next_attempt_time = 0.0

  def _schedule_retry(self) -> None:
    delay = min(NAV_RETRY.BASE_SECONDS * (2 ** self.failed_attempts), NAV_RETRY.MAX_SECONDS)
    self.failed_attempts += 1
    self.next_attempt_time = monotonic() + delay
    cloudlog.warning("navd: no route for destination %r, retrying in %.0fs (attempt %d)",
                     self.new_destination, delay, self.failed_attempts)

  def _update_params(self):
    if self.last_position is not None:
      self.frame += 1
      if self.frame % 15 == 0:
        self.allow_navigation = self.params.get('AllowNavigation', return_default=True)
        self.new_destination = self.params.get('MapboxRoute')
        self.recompute_allowed = self.params.get('MapboxRecompute', return_default=True)

      # the destination can be cleared externally (e.g. from the settings UI), so drop the active route.
      # Params returns None for an unset or empty string param, so treat both as "no destination"
      if not self.new_destination and self.route is not None:
        self._drop_route()

      # entering a different destination is a fresh request, so it must not inherit the
      # backoff accumulated by the previous one
      if self.new_destination != self.attempted_destination:
        self._reset_retry()

      # a destination is only accepted once a route actually came back, so a directions failure
      # stays pending instead of latching a destination that can never be recomputed
      pending = bool(self.new_destination) and self.new_destination != self.destination
      rerouting = bool(self.recompute_allowed and not self.final_step and self.reroute_counter > 9 and self.route)
      self.allow_recompute: bool = (pending or rerouting) and monotonic() >= self.next_attempt_time

      if self.allow_recompute:
        self.attempted_destination = self.new_destination
        postvars = {'place_name': self.new_destination}
        postvars, route_ready = self.mapbox.set_destination(postvars, self.last_position.longitude, self.last_position.latitude, self.last_bearing)

        route = None
        if route_ready:
          self.nav_instructions.clear_route_cache()
          route = self.nav_instructions.get_current_route()

        if route is not None:
          self.destination = self.new_destination
          self.route = route
          self.cancel_route_counter = 0
          self.reroute_counter = 0
          self._reset_retry()
        else:
          # an existing route is left alone: only the request for a new one failed
          self._schedule_retry()

      if self.cancel_route_counter == 30:
        self.params.put("MapboxRoute", "")
        self._drop_route()

      self.valid = self.route is not None

  def _update_navigation(self) -> tuple[str, dict | None, dict]:
    banner_instructions: str = ''
    nav_data: dict = {}
    if self.allow_navigation and self.route and self.last_position is not None:
      if progress := self.nav_instructions.get_route_progress(self.last_position.latitude, self.last_position.longitude):
        v_ego = float(max(self.sm['carState'].vEgo, 0.0))
        nav_data['upcoming_turn'] = self.nav_instructions.get_upcoming_turn_from_progress(progress, self.last_position.latitude,
                                                                                          self.last_position.longitude, v_ego)
        speed_limit, _ = progress['current_maxspeed']
        nav_data['current_speed_limit'] = speed_limit
        arrived = self.nav_instructions.arrived_at_destination(progress, v_ego)

        if progress['current_step']:
          parsed = parse_banner_instructions(progress['current_step']['bannerInstructions'], progress['distance_to_end_of_step'])
          if parsed:
            banner_instructions = parsed['maneuverPrimaryText']

        nav_data['distance_from_route'] = progress['distance_from_route']
        nav_data['distance_remaining'] = progress['distance_remaining']
        nav_data['time_remaining'] = progress['time_remaining']
        speed_breakpoints: list = [0.0, 5.0, 10.0, 20.0, 40.0]
        distance_list: list = [100.0, 125.0, 150.0, 200.0, 250.0]
        large_distance: bool = progress['distance_from_route'] > float(interp(v_ego, speed_breakpoints, distance_list))

        route_bearing_misalign: bool = self.nav_instructions.route_bearing_misalign(self.route, self.last_bearing, v_ego)

        if large_distance and not arrived:
          self.cancel_route_counter = self.cancel_route_counter + 1 if progress['distance_from_route'] > NAV_CV.QUARTER_MILE else 0
          if self.recompute_allowed:
            self.reroute_counter += 1
        elif arrived:
          self.cancel_route_counter += 1
          self.recompute_allowed = False
        elif route_bearing_misalign:
          self.cancel_route_counter += 1
          if self.recompute_allowed:
            self.reroute_counter += 1
        else:
          self.cancel_route_counter = 0
          self.reroute_counter = 0

        # recomputing from inside the final step causes reroute loops at the destination, so
        # gate it with a dedicated latch. The param-backed allow_navigation/recompute_allowed
        # flags can't hold this state: they flap back on at the next 5s param re-read, which
        # made banners and arrival cleanup fire only on the iterations where the flap was off
        self.final_step = progress['current_step_idx'] == len(self.route['steps']) - 1
    else:
      banner_instructions = ''
      progress = None
      nav_data = {}

    return banner_instructions, progress, nav_data

  def _build_navigation_message(self, banner_instructions: str, progress: dict | None, nav_data: dict, valid: bool):
    msg = messaging.new_message('navigationd')
    msg.valid = valid
    msg.navigationd.upcomingTurn = nav_data.get('upcoming_turn', 'none')
    msg.navigationd.currentSpeedLimit = nav_data.get('current_speed_limit', 0)
    msg.navigationd.bannerInstructions = banner_instructions
    msg.navigationd.distanceFromRoute = nav_data.get('distance_from_route', 0.0)
    msg.navigationd.distanceRemaining = nav_data.get('distance_remaining', 0.0)
    msg.navigationd.timeRemaining = nav_data.get('time_remaining', 0.0)
    msg.navigationd.valid = self.valid
    msg.navigationd.routeFailures = min(self.failed_attempts, 0xffff)

    all_maneuvers = (
      [custom.Navigationd.Maneuver.new_message(distance=m['distance'], type=m['type'], modifier=m['modifier'],
                                               instruction=m['instruction']) for m in progress['all_maneuvers']]
      if progress
      else []
    )
    msg.navigationd.allManeuvers = all_maneuvers
    return msg

  def run(self):
    cloudlog.warning('navigationd init')

    while True:
      self.sm.update(0)
      location = self.sm['liveLocationKalman']
      localizer_valid = location.positionGeodetic.valid if location else False

      if localizer_valid:
        self.last_bearing = degrees(location.calibratedOrientationNED.value[2])
        self.last_position = Coordinate(location.positionGeodetic.value[0], location.positionGeodetic.value[1])

      self._update_params()
      banner_instructions, progress, nav_data = self._update_navigation()

      msg = self._build_navigation_message(banner_instructions, progress, nav_data, valid=localizer_valid)

      self.pm.send('navigationd', msg)
      self.rk.keep_time()


def main():
  nav = Navigationd()
  nav.run()
