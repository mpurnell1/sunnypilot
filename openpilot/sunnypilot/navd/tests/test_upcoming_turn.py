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
