"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

A picked route is pinned by one coordinate on it, sent to Directions as a silent waypoint,
so the road the driver chose is routed through whatever alternates Mapbox offers later.
"""
from openpilot.sunnypilot.navd.helpers import Coordinate

# distances compare every point of a route against a sample of each other route
SAMPLE_POINTS = 100
# a pin this close to the car or to the destination adds nothing and can only detour
PIN_MARGIN_M = 50.0


def _sample(points: list[Coordinate]) -> list[Coordinate]:
  stride = max(1, len(points) // SAMPLE_POINTS)
  return points[::stride]


def pin_point(points: list[Coordinate], others: list[list[Coordinate]]) -> Coordinate | None:
  """The point of the route farthest from every other alternate; the middle point when it is alone."""
  if not points:
    return None
  samples = [_sample(o) for o in others if o]
  if not samples:
    return points[len(points) // 2]
  return max(_sample(points), key=lambda p: min(p.distance_to(q) for other in samples for q in other))


def pin_ahead(start: Coordinate, pin: Coordinate, end: Coordinate) -> bool:
  """Whether the pin still lies between the car and the destination, so a reroute keeps it."""
  to_end = pin.distance_to(end)
  return start.distance_to(pin) > PIN_MARGIN_M and to_end > PIN_MARGIN_M and start.distance_to(end) > to_end + PIN_MARGIN_M


def parse_pin(value: object) -> Coordinate | None:
  """A "lon,lat" string, the same shape as a coordinate destination, to a Coordinate."""
  if not isinstance(value, str):
    return None
  parts = value.split(",")
  if len(parts) != 2:
    return None
  try:
    return Coordinate(float(parts[1]), float(parts[0]))
  except ValueError:
    return None
