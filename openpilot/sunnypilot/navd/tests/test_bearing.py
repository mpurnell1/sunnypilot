"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pytest

from openpilot.sunnypilot.navd.helpers import Coordinate, bearing_between_two_points
from openpilot.sunnypilot.navd.navigation_helpers.nav_instructions import NavigationInstructions

ORIGIN = Coordinate(40.1, -88.2)


@pytest.mark.parametrize("dlat, dlon, expected", [
  (0.01, 0.0, 0.0),
  (0.0, 0.01, 90.0),
  (-0.01, 0.0, 180.0),
  (0.0, -0.01, 270.0),
  (0.01, 0.01, 37.4),
])
def test_compass_bearings_at_mid_latitude(dlat, dlon, expected):
  # the longitude leg of a diagonal shrinks by cos(lat), so NE is not 45 here
  bearing = bearing_between_two_points(ORIGIN, Coordinate(ORIGIN.latitude + dlat, ORIGIN.longitude + dlon))
  assert bearing == pytest.approx(expected, abs=0.2)


def test_driving_along_an_eastbound_route_is_aligned():
  nav = NavigationInstructions()
  geometry = [Coordinate(ORIGIN.latitude, ORIGIN.longitude + 0.001 * i) for i in range(4)]
  route = {'bearings': [bearing_between_two_points(geometry[i], geometry[i + 2]) for i in range(len(geometry) - 2)]}
  nav.closest_idx = 0
  assert not nav.route_bearing_misalign(route, 90.0, 20.0)
  assert nav.route_bearing_misalign(route, 270.0, 20.0)
