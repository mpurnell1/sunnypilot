"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigation_helpers.route_pin import parse_pin, pin_ahead, pin_point

# two routes north from (0, 0) to (0.02, 0): one straight up the meridian, one bowing east
STRAIGHT = [Coordinate(0.0, 0.0), Coordinate(0.005, 0.0), Coordinate(0.01, 0.0), Coordinate(0.015, 0.0), Coordinate(0.02, 0.0)]
BOWED = [Coordinate(0.0, 0.0), Coordinate(0.005, 0.004), Coordinate(0.01, 0.008), Coordinate(0.015, 0.004), Coordinate(0.02, 0.0)]


class TestPinPoint:
  def test_the_pin_is_where_the_routes_differ_most(self):
    pin = pin_point(BOWED, [STRAIGHT])
    assert (pin.latitude, pin.longitude) == (0.01, 0.008)
    pin = pin_point(STRAIGHT, [BOWED])
    assert pin.longitude == 0.0 and pin.latitude == 0.01

  def test_a_lone_route_pins_its_middle(self):
    assert pin_point(STRAIGHT, []) is STRAIGHT[2]
    assert pin_point(STRAIGHT, [[]]) is STRAIGHT[2]

  def test_no_points_no_pin(self):
    assert pin_point([], [STRAIGHT]) is None


class TestPinAhead:
  def test_a_pin_between_car_and_destination_holds(self):
    assert pin_ahead(Coordinate(0.0, 0.0), Coordinate(0.01, 0.008), Coordinate(0.02, 0.0))

  def test_a_pin_behind_the_car_is_dropped(self):
    assert not pin_ahead(Coordinate(0.015, 0.004), Coordinate(0.01, 0.008), Coordinate(0.02, 0.0))

  def test_a_pin_under_the_car_or_at_the_destination_is_dropped(self):
    assert not pin_ahead(Coordinate(0.01, 0.008), Coordinate(0.01, 0.0081), Coordinate(0.02, 0.0))
    assert not pin_ahead(Coordinate(0.0, 0.0), Coordinate(0.02, 0.0001), Coordinate(0.02, 0.0))


class TestParsePin:
  def test_lon_lat_string_round_trips(self):
    pin = parse_pin("-88.243695,40.105587")
    assert (pin.longitude, pin.latitude) == (-88.243695, 40.105587)

  def test_anything_else_is_no_pin(self):
    assert parse_pin("") is None
    assert parse_pin("north") is None
    assert parse_pin("1,2,3") is None
    assert parse_pin(None) is None
