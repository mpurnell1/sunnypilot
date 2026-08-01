"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.cereal import custom

from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import ARROW_ANGLES, format_distance, pick_upcoming_maneuver
from openpilot.selfdrive.ui.sunnypilot.onroad.route_summary import format_remaining_time
from openpilot.sunnypilot.navd.helpers import string_to_direction


def _maneuvers(*specs):
  msg = custom.Navigationd.new_message()
  msg.init('allManeuvers', len(specs))
  for m, (maneuver_type, modifier, distance) in zip(msg.allManeuvers, specs, strict=True):
    m.type = maneuver_type
    m.modifier = modifier
    m.distance = distance
  return msg.allManeuvers


class TestFormatDistance:
  def test_metric(self):
    assert format_distance(432, True) == "430 m"
    assert format_distance(996, True) == "1000 m"
    assert format_distance(1230, True) == "1.2 km"
    assert format_distance(15400, True) == "15 km"

  def test_imperial(self):
    assert format_distance(100, False) == "350 ft"
    assert format_distance(304, False) == "1000 ft"
    assert format_distance(305, False) == "0.2 mi"
    assert format_distance(500, False) == "0.3 mi"
    assert format_distance(17000, False) == "11 mi"

  def test_never_negative(self):
    assert format_distance(-5, True) == "0 m"
    assert format_distance(-5, False) == "0 ft"


class TestFormatRemainingTime:
  def test_minutes(self):
    assert format_remaining_time(0) == "0 min"
    assert format_remaining_time(59) == "1 min"
    assert format_remaining_time(25 * 60) == "25 min"

  def test_hours(self):
    assert format_remaining_time(3600) == "1 hr 0 min"
    assert format_remaining_time(4980) == "1 hr 23 min"
    assert format_remaining_time(7260) == "2 hr 1 min"


class TestPickUpcomingManeuver:
  def test_prefers_second_entry(self):
    picked = pick_upcoming_maneuver(_maneuvers(('depart', 'none', 120.0), ('turn', 'right', 480.0)))
    assert picked is not None
    maneuver_type, modifier, distance = picked
    assert (maneuver_type, modifier) == ('turn', 'right')
    assert abs(distance - 480.0) < 1e-6

  def test_lone_arrive_still_shows(self):
    picked = pick_upcoming_maneuver(_maneuvers(('arrive', 'none', 60.0)))
    assert picked is not None
    assert picked[0] == 'arrive'

  def test_lone_non_arrive_hidden(self):
    assert pick_upcoming_maneuver(_maneuvers(('depart', 'none', 120.0))) is None

  def test_empty_hidden(self):
    assert pick_upcoming_maneuver(_maneuvers()) is None


class TestArrowVocabulary:
  def test_covers_every_normalized_modifier(self):
    # navigationd publishes string_to_direction's output verbatim, so every value it can
    # produce must either map to an angle or have a dedicated glyph ('uturn')
    raw_modifiers = ['left', 'right', 'straight', 'slight left', 'slight right',
                     'sharp left', 'sharp right', '']
    for raw in raw_modifiers:
      assert string_to_direction(raw) in ARROW_ANGLES

  def test_uturn_is_not_flattened(self):
    assert string_to_direction('uturn') == 'uturn'
