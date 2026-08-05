"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from concurrent.futures import Future, ThreadPoolExecutor
from math import degrees
from numpy import interp
from time import monotonic

import openpilot.cereal.messaging as messaging
from openpilot.cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

from openpilot.sunnypilot.navd.constants import NAV_RETRY

# a lane-change direction must hold this many 3Hz cycles before it publishes: hints were
# observed flapping left/right within a second, and a flap that reaches the assist gate
# flips which blinker can start a lane change
HINT_STABLE_CYCLES = 3
from openpilot.sunnypilot.navd.helpers import Coordinate, lane_change_auto_confirm, lane_change_hint, parse_banner_instructions
from openpilot.sunnypilot.navd.constants import LANE_GUIDANCE_ASSIST
from openpilot.sunnypilot.navd.nav_audio import NavAudioCues
from openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration import MapboxIntegration
from openpilot.sunnypilot.navd.navigation_helpers.nav_instructions import NavigationInstructions


class Navigationd:
  def __init__(self):
    self.params = Params()
    self.mapbox = MapboxIntegration()
    self.nav_instructions = NavigationInstructions()
    self.nav_audio = NavAudioCues()

    self.sm = messaging.SubMaster(['carState', 'liveLocationKalman'])
    self.pm = messaging.PubMaster(['navigationd'])
    self.rk = Ratekeeper(3) # 3 Hz

    self.route = None
    self.destination: str | None = None
    self.new_destination: str = ''

    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='navd_route')
    self.route_request: Future | None = None

    self.allow_navigation: bool = False
    self.lane_guidance: int = 0  # 0 off, 1 display, 2 display + assist
    self.recompute_allowed: bool = False
    self.allow_recompute: bool = False
    self.reroute_counter: int = 0
    self.arrival_counter: int = 0
    self.empty_destination_reads: int = 0
    self.observed_destination: str | None = None
    self.attempted_destination: str | None = None
    self.failed_attempts: int = 0
    self.next_attempt_time: float = 0.0
    self.final_step: bool = False
    self.rerouting: bool = False

    self.frame: int = -1
    self.last_position: Coordinate | None = None
    self.last_bearing: float | None = None
    self.valid: bool = False

    self.hint_candidate: str = 'none'
    self.hint_stable_count: int = 0
    self.published_hint: str = 'none'

  # MapboxSettings outlives the in-memory route, so a route left there can be reloaded later
  def _drop_route(self) -> None:
    self.params.remove("MapboxSettings")
    self.nav_instructions.clear_route_cache()
    self.route = None
    self.destination = None
    # MapboxRoute is only re-read every 15 frames, so until the next poll the stale in-memory
    # value still reads as a fresh destination and re-requests the trip that just concluded
    self.new_destination = ''
    self.arrival_counter = 0
    self.reroute_counter = 0
    self.empty_destination_reads = 0
    self.final_step = False
    self._reset_hint()

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
        self.lane_guidance = self.params.get('NavLaneGuidance', return_default=True)
        self.new_destination = self.params.get('MapboxRoute')
        self.recompute_allowed = self.params.get('MapboxRecompute', return_default=True)

        # audit trail: an unattributed one-poll empty read killed a live highway route on
        # 2026-08-03, so every observed destination change is worth a log line
        if self.new_destination != self.observed_destination:
          cloudlog.warning("navd: destination param changed %r -> %r", self.observed_destination, self.new_destination)
          self.observed_destination = self.new_destination

        # the destination can be cleared externally (e.g. from the settings UI), and Params
        # returns None for an unset or empty string param, so treat both as "no destination".
        # A single empty read is not proof of intent: the route only drops once the clear
        # persists across polls, so a read glitch costs nothing but one poll of latency
        if not self.new_destination and self.route is not None:
          self.empty_destination_reads += 1
          if self.empty_destination_reads >= 2:
            cloudlog.warning("navd: destination stayed empty across polls, dropping the route")
            self._drop_route()
        else:
          self.empty_destination_reads = 0

      # entering a different destination is a fresh request, so it must not inherit the
      # backoff accumulated by the previous one
      if self.new_destination != self.attempted_destination:
        self._reset_retry()

      # a destination is only accepted once a route actually came back, so a directions failure
      # stays pending instead of latching a destination that can never be recomputed
      pending = bool(self.new_destination) and self.new_destination != self.destination
      rerouting = bool(self.recompute_allowed and not self.final_step and self.reroute_counter > 9 and self.route)
      self.rerouting = rerouting
      self.allow_recompute: bool = (pending or rerouting) and monotonic() >= self.next_attempt_time

      # requests run off the loop: geocoding + directions + timezone can block 15s on a dead
      # LTE link, and an on-loop request once froze banners and hints for 6s at 81mph
      if self.allow_recompute and self.route_request is None:
        self.attempted_destination = self.new_destination
        postvars = {'place_name': self.new_destination}
        self.route_request = self.executor.submit(self.mapbox.set_destination, postvars,
                                                  self.last_position.longitude, self.last_position.latitude, self.last_bearing)

      if self.route_request is not None and self.route_request.done():
        request, self.route_request = self.route_request, None
        try:
          _, route_ready = request.result()
        except Exception:
          cloudlog.exception("navd: route request raised")
          route_ready = False

        # a result for a destination the driver has since changed or cleared must not land
        if self.attempted_destination == self.new_destination:
          route = None
          if route_ready:
            self.nav_instructions.clear_route_cache()
            route = self.nav_instructions.get_current_route()

          if route is not None:
            self.destination = self.new_destination
            self.route = route
            self.arrival_counter = 0
            self.reroute_counter = 0
            self._reset_retry()
          else:
            # an existing route is left alone: only the request for a new one failed
            self._schedule_retry()

      # arrival is the only condition that concludes a trip on its own; clearing the
      # destination param here is what lets the same address start a fresh route later
      if self.arrival_counter >= 30:
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
        nav_data['arrived'] = arrived

        banner_lanes = None
        if progress['current_step']:
          parsed = parse_banner_instructions(progress['current_step']['bannerInstructions'], progress['distance_to_end_of_step'])
          if parsed:
            banner_instructions = parsed['maneuverPrimaryText']
            # the lane layout is a static property of the approach, so the confirm gate may
            # read it from any of the step's banners, not just the active one
            banner_lanes = parsed.get('lanes')
            # showFull means the banner for this distance is active; earlier banners on the same
            # step describe the maneuver from too far out for lane-level advice to apply yet
            if self.lane_guidance and parsed.get('showFull'):
              nav_data['lanes'] = banner_lanes or []

        nav_data['distance_from_route'] = progress['distance_from_route']
        nav_data['distance_remaining'] = progress['distance_remaining']
        nav_data['time_remaining'] = progress['time_remaining']

        speed_breakpoints: list = [0.0, 5.0, 10.0, 20.0, 40.0]
        distance_list: list = [100.0, 125.0, 150.0, 200.0, 250.0]
        large_distance: bool = progress['distance_from_route'] > float(interp(v_ego, speed_breakpoints, distance_list))

        route_bearing_misalign: bool = self.nav_instructions.route_bearing_misalign(self.route, self.last_bearing, v_ego)

        if self.lane_guidance >= LANE_GUIDANCE_ASSIST:
          # a hint is only as good as the route it came from: off it, pointed away from it,
          # or waiting on a failed reroute, the stale route must not prompt lane changes
          route_trusted = not large_distance and not route_bearing_misalign and self.failed_attempts == 0
          hint = self._stable_hint(lane_change_hint(progress, v_ego) if route_trusted else 'none')
          nav_data['lane_change_direction'] = hint
          nav_data['lane_change_auto_confirm'] = hint != 'none' and lane_change_auto_confirm(progress, banner_lanes)

        # being lost never cancels the route: off-route and misalignment only ask for a
        # recompute, and with no network the route is held so guidance returns on its own
        if large_distance and not arrived:
          if self.recompute_allowed:
            self.reroute_counter += 1
        elif arrived:
          self.arrival_counter += 1
          self.recompute_allowed = False
        elif route_bearing_misalign:
          if self.recompute_allowed:
            self.reroute_counter += 1
        else:
          self.arrival_counter = 0
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
      self._reset_hint()

    return banner_instructions, progress, nav_data

  def _reset_hint(self) -> None:
    self.hint_candidate = 'none'
    self.hint_stable_count = 0
    self.published_hint = 'none'

  def _stable_hint(self, hint: str) -> str:
    if hint == self.hint_candidate:
      self.hint_stable_count += 1
    else:
      self.hint_candidate = hint
      self.hint_stable_count = 1

    if hint == 'none' or self.hint_stable_count >= HINT_STABLE_CYCLES:
      self.published_hint = hint
    elif self.published_hint != hint:
      # between directions the safe output is no hint, not the stale one
      self.published_hint = 'none'
    return self.published_hint

  def _build_navigation_message(self, banner_instructions: str, progress: dict | None, nav_data: dict, valid: bool):
    msg = messaging.new_message('navigationd')
    msg.valid = valid
    msg.navigationd.upcomingTurn = nav_data.get('upcoming_turn', 'none')
    msg.navigationd.currentSpeedLimit = nav_data.get('current_speed_limit', 0)
    msg.navigationd.bannerInstructions = banner_instructions
    msg.navigationd.distanceFromRoute = nav_data.get('distance_from_route', 0.0)
    msg.navigationd.distanceRemaining = nav_data.get('distance_remaining', 0.0)
    msg.navigationd.timeRemaining = nav_data.get('time_remaining', 0.0)
    msg.navigationd.laneChangeDirection = nav_data.get('lane_change_direction', 'none')
    msg.navigationd.laneChangeAutoConfirm = nav_data.get('lane_change_auto_confirm', False)
    msg.navigationd.valid = self.valid
    msg.navigationd.routeFailures = min(self.failed_attempts, 0xffff)
    msg.navigationd.audioCueCode = self.nav_audio.code
    msg.navigationd.audioCueStage = self.nav_audio.stage
    msg.navigationd.audioCueId = self.nav_audio.cue_id

    all_maneuvers = (
      [custom.Navigationd.Maneuver.new_message(distance=m['distance'], type=m['type'], modifier=m['modifier'],
                                               instruction=m['instruction']) for m in progress['all_maneuvers']]
      if progress
      else []
    )
    msg.navigationd.allManeuvers = all_maneuvers
    msg.navigationd.lanes = [
      custom.Navigationd.LaneGuidance.new_message(directions=lane['directions'], active=lane['active'],
                                                  activeDirection=lane.get('activeDirection', ''))
      for lane in nav_data.get('lanes', [])
    ]
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
      self.nav_audio.update(self.route, progress, nav_data, float(max(self.sm['carState'].vEgo, 0.0)), self.rerouting)

      msg = self._build_navigation_message(banner_instructions, progress, nav_data, valid=localizer_valid)

      self.pm.send('navigationd', msg)
      self.rk.keep_time()


def main():
  nav = Navigationd()
  nav.run()
