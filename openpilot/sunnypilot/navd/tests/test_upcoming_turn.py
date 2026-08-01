"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigation_helpers.nav_instructions import NavigationInstructions

LAT, LON = 32.7767, -96.797


def _progress(maneuver_type: str, modifier: str) -> dict:
  # next turn at the current position, so the distance gate always passes
  return {'next_turn': {'location': Coordinate(LAT, LON), 'maneuver': maneuver_type, 'modifier': modifier}}


class TestUpcomingTurn:
  def setup_method(self):
    self.nav = NavigationInstructions()

  def test_plain_turn_publishes_modifier(self):
    assert self.nav.get_upcoming_turn_from_progress(_progress('turn', 'right'), LAT, LON, 0.0) == 'right'

  def test_uturn_survives(self):
    assert self.nav.get_upcoming_turn_from_progress(_progress('turn', 'uturn'), LAT, LON, 0.0) == 'uturn'

  def test_roundabout_overrides_modifier(self):
    # the modifier is only the exit heading, so it must not read as an ordinary turn
    for maneuver_type in ('roundabout', 'rotary', 'roundabout turn', 'exit roundabout', 'exit rotary'):
      assert self.nav.get_upcoming_turn_from_progress(_progress(maneuver_type, 'slightRight'), LAT, LON, 0.0) == 'roundabout'

  def test_far_turn_stays_hidden(self):
    progress = _progress('turn', 'right')
    progress['next_turn']['location'] = Coordinate(LAT + 0.1, LON)
    assert self.nav.get_upcoming_turn_from_progress(progress, LAT, LON, 0.0) == 'none'


class TestRouteRemaining:
  def setup_method(self):
    self.nav = NavigationInstructions()

    # straight north-south route: 11 points, two 60-second driving steps plus the arrive step
    geometry = [Coordinate(LAT + 0.001 * i, LON) for i in range(11)]
    cumulative = [0.0]
    for i in range(1, len(geometry)):
      cumulative.append(cumulative[-1] + geometry[i - 1].distance_to(geometry[i]))
    self.total = cumulative[-1]
    self.halfway = cumulative[5]

    def step(idx, distance, duration, maneuver):
      return {'cumulative_distance': cumulative[idx], 'distance': distance, 'duration': duration,
              'maneuver': maneuver, 'modifier': 'none', 'instruction': '', 'bannerInstructions': [],
              'location': geometry[idx], 'maxspeed': (0, 'kmh')}

    self.nav._cached_route = {
      'geometry': geometry,
      'cumulative_distances': cumulative,
      'total_distance': self.total,
      'total_duration': 120.0,
      'maxspeed': [],
      'bearings': [],
      'steps': [step(0, self.halfway, 60.0, 'depart'),
                step(5, self.total - self.halfway, 60.0, 'turn'),
                step(10, 0.0, 0.0, 'arrive')],
    }
    self.nav._route_loaded = True

  def test_at_start(self):
    progress = self.nav.get_route_progress(LAT, LON)
    assert abs(progress['distance_remaining'] - self.total) < 1.0
    assert abs(progress['time_remaining'] - 120.0) < 1.0

  def test_halfway(self):
    progress = self.nav.get_route_progress(LAT + 0.005, LON)
    assert abs(progress['distance_remaining'] - (self.total - self.halfway)) < 1.0
    assert abs(progress['time_remaining'] - 60.0) < 1.0

  def test_at_destination(self):
    progress = self.nav.get_route_progress(LAT + 0.010, LON)
    assert progress['distance_remaining'] < 1.0
    assert progress['time_remaining'] < 1.0
