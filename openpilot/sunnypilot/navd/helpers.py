from __future__ import annotations

import json
import math
import numpy as np
from typing import Any, cast

from openpilot.common.constants import CV
from openpilot.common.params import Params

DIRECTIONS = ('left', 'right', 'straight')
MODIFIABLE_DIRECTIONS = ('left', 'right')
# substring-matched against Mapbox maneuver types, so these also cover
# 'roundabout turn', 'exit roundabout', and 'exit rotary'
ROUNDABOUT_TYPES = ('roundabout', 'rotary')

# maneuvers where being in the correct lane early matters; roundabouts are excluded because
# the useful lane depends on the exit, which the modifier alone does not describe
LANE_CHANGE_HINT_TYPES = ('turn', 'off ramp', 'fork', 'merge', 'end of road', 'continue')
# uturn maps to left for right-hand-traffic countries
LANE_CHANGE_HINT_SIDES = {'slightLeft': 'left', 'left': 'left', 'sharpLeft': 'left', 'uturn': 'left',
                          'slightRight': 'right', 'right': 'right', 'sharpRight': 'right'}
LANE_CHANGE_HINT_SPEED_BP = [8.0, 15.0, 25.0, 35.0]  # m/s
LANE_CHANGE_HINT_DIST = [150.0, 300.0, 600.0, 900.0]  # m
# maneuvers that can only happen from a slip road or multi-lane carriageway, so the adjacent
# lane is guaranteed to run our way; every other hint type can occur on a two-lane road where
# the space beside us is oncoming traffic
LANE_CHANGE_CONFIRM_TYPES = ('off ramp', 'merge')

EARTH_MEAN_RADIUS = 6371007.2
SPEED_CONVERSIONS = {
  'km/h': CV.KPH_TO_MS,
  'mph': CV.MPH_TO_MS,
}


class Coordinate:
  def __init__(self, latitude: float, longitude: float) -> None:
    self.latitude = latitude
    self.longitude = longitude
    self.annotations: dict[str, float] = {}

  @classmethod
  def from_mapbox_tuple(cls, t: tuple[float, float]) -> Coordinate:
    return cls(t[1], t[0])

  def as_dict(self) -> dict[str, float]:
    return {'latitude': self.latitude, 'longitude': self.longitude}

  def __str__(self) -> str:
    return f'Coordinate({self.latitude}, {self.longitude})'

  def __repr__(self) -> str:
    return self.__str__()

  def __eq__(self, other) -> bool:
    if not isinstance(other, Coordinate):
      return False
    return (self.latitude == other.latitude) and (self.longitude == other.longitude)

  def __sub__(self, other: Coordinate) -> Coordinate:
    return Coordinate(self.latitude - other.latitude, self.longitude - other.longitude)

  def __add__(self, other: Coordinate) -> Coordinate:
    return Coordinate(self.latitude + other.latitude, self.longitude + other.longitude)

  def __mul__(self, c: float) -> Coordinate:
    return Coordinate(self.latitude * c, self.longitude * c)

  def dot(self, other: Coordinate) -> float:
    return self.latitude * other.latitude + self.longitude * other.longitude

  def distance_to(self, other: Coordinate) -> float:
    # Haversine formula
    dlat = math.radians(other.latitude - self.latitude)
    dlon = math.radians(other.longitude - self.longitude)

    haversine_dlat = math.sin(dlat / 2.0)
    haversine_dlat *= haversine_dlat
    haversine_dlon = math.sin(dlon / 2.0)
    haversine_dlon *= haversine_dlon

    y = haversine_dlat \
        + math.cos(math.radians(self.latitude)) \
        * math.cos(math.radians(other.latitude)) \
        * haversine_dlon
    x = 2 * math.asin(math.sqrt(y))
    return x * EARTH_MEAN_RADIUS


def bearing_between_two_points(point_one: Coordinate, point_two: Coordinate) -> float:
  dlon = math.radians(point_two.longitude - point_one.longitude)
  bearing_radians = math.atan2(math.sin(dlon)* math.cos(point_two.latitude), math.cos(point_one.latitude) * math.sin(point_two.latitude) -
                               math.sin(point_one.latitude) * math.cos(point_two.latitude) * math.cos(dlon))
  bearing_degrees = math.degrees(bearing_radians)
  bearing_normalized = (bearing_degrees + 360) % 360
  return bearing_normalized


def project_onto_geometry(geometry: list[Coordinate], cumulative_distances: list[float], pos: Coordinate) -> tuple[float, int, float]:
  """Closest point on the route polyline: (crosstrack distance, segment index, distance along the route).

  The distance to the nearest vertex is not usable as an off-route measure: interstate
  polylines space vertices 400m+ apart, so a vertex distance sawtooths by hundreds of
  meters while the car drives dead-centre on the route.
  """
  if len(geometry) < 2:
    return (geometry[0].distance_to(pos) if geometry else 0.0), 0, 0.0

  # equirectangular projection about the vehicle: over one segment the flat-earth error is
  # negligible next to the thresholds these outputs feed, and longitude must be scaled by
  # cos(lat) or east-west displacement outweighs north-south in the dot products
  k = math.cos(math.radians(pos.latitude))
  px, py = pos.longitude * k, pos.latitude

  best_distance, best_idx, best_t = float('inf'), 0, 0.0
  for i in range(len(geometry) - 1):
    a, b = geometry[i], geometry[i + 1]
    ax, ay = a.longitude * k, a.latitude
    bx, by = b.longitude * k, b.latitude
    seg2 = (bx - ax) ** 2 + (by - ay) ** 2
    t = 0.0 if seg2 == 0.0 else min(max(((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / seg2, 0.0), 1.0)
    projection = Coordinate(a.latitude + (b.latitude - a.latitude) * t, a.longitude + (b.longitude - a.longitude) * t)
    d = projection.distance_to(pos)
    if d < best_distance:
      best_distance, best_idx, best_t = d, i, t

  along = cumulative_distances[best_idx] + best_t * (cumulative_distances[best_idx + 1] - cumulative_distances[best_idx])
  return best_distance, best_idx, along


def coordinate_from_param(param: str, params: Params = None) -> Coordinate | None:
  if params is None:
    params = Params()

  json_str = params.get(param)
  if json_str is None:
    return None

  pos = json.loads(json_str)
  if 'latitude' not in pos or 'longitude' not in pos:
    return None

  return Coordinate(pos['latitude'], pos['longitude'])


def lane_change_hint(progress: dict, v_ego: float) -> str:
  """Which side the route wants the car on for the next maneuver, or 'none'.

  Purely advisory: the consumer only uses it to confirm a lane change the driver has
  already signaled, so a wrong hint can at worst leave the normal nudge flow in place.
  """
  maneuvers = progress['all_maneuvers']
  if len(maneuvers) < 2:
    return 'none'
  m = maneuvers[1]
  if not any(t in m['type'] for t in LANE_CHANGE_HINT_TYPES):
    return 'none'
  side = LANE_CHANGE_HINT_SIDES.get(m['modifier'])
  if side is None:
    return 'none'
  window = np.interp(v_ego, LANE_CHANGE_HINT_SPEED_BP, LANE_CHANGE_HINT_DIST)
  return side if m['distance'] <= window else 'none'


def lane_change_auto_confirm(progress: dict, banner_lanes: list | None) -> bool:
  """Whether an active hint may stand in for the wheel nudge.

  The display side is untouched by this: the banner prompts for every hint type, but only a
  maneuver that proves a same-direction adjacent lane exists — by topology, or by the map
  showing a multi-lane approach — lets the blinker alone start the change. The model's
  road-edge check cannot make this call: an oncoming lane is a full-width lane.
  """
  maneuvers = progress['all_maneuvers']
  if len(maneuvers) < 2:
    return False
  m = maneuvers[1]
  # a u-turn's "adjacent lane" is oncoming by definition, whatever the map says
  if m['modifier'] == 'uturn':
    return False
  if any(t in m['type'] for t in LANE_CHANGE_CONFIRM_TYPES):
    return True
  # Mapbox emits lane banners exactly at multi-lane approaches and omits them on
  # two-lane roads, so they double as a same-direction discriminator
  return banner_lanes is not None and len(banner_lanes) >= 2


def string_to_direction(direction: str) -> str:
  # matched before the left/right scan: Mapbox's uturn modifier contains neither word, and
  # flattening it to 'none' would render a u-turn as "continue straight"
  if 'uturn' in direction:
    return 'uturn'
  for d in DIRECTIONS:
    if d in direction:
      if 'slight' in direction and d in MODIFIABLE_DIRECTIONS:
        return 'slight' + d.capitalize()
      elif 'sharp' in direction and d in MODIFIABLE_DIRECTIONS:
        return 'sharp' + d.capitalize()
      return d
  return 'none'


def maxspeed_to_ms(maxspeed: dict[str, str | float]) -> float:
  unit = cast(str, maxspeed['unit'])
  speed = cast(float, maxspeed['speed'])
  return float(SPEED_CONVERSIONS[unit] * speed)


def field_valid(dat: dict, field: str) -> bool:
  return field in dat and dat[field] is not None


def parse_banner_instructions(banners: Any, distance_to_maneuver: float = 0.0) -> dict[str, Any] | None:
  if not len(banners):
    return None

  instruction = {}

  # A segment can contain multiple banners, find one that we need to show now
  current_banner = banners[0]
  for banner in banners:
    if distance_to_maneuver < banner['distanceAlongGeometry']:
      current_banner = banner

  # Only show banner when close enough to maneuver
  instruction['showFull'] = distance_to_maneuver < current_banner['distanceAlongGeometry']

  # Primary
  p = current_banner['primary']
  if field_valid(p, 'text'):
    instruction['maneuverPrimaryText'] = p['text']
  if field_valid(p, 'type'):
    instruction['maneuverType'] = p['type']
  if field_valid(p, 'modifier'):
    instruction['maneuverModifier'] = p['modifier']

  # Secondary
  if field_valid(current_banner, 'secondary'):
    instruction['maneuverSecondaryText'] = current_banner['secondary']['text']

  # Lane lines
  if field_valid(current_banner, 'sub'):
    lanes = []
    for component in current_banner['sub']['components']:
      if component['type'] != 'lane':
        continue

      lane = {
        'active': component['active'],
        'directions': [string_to_direction(d) for d in component['directions']],
      }

      if field_valid(component, 'active_direction'):
        lane['activeDirection'] = string_to_direction(component['active_direction'])

      lanes.append(lane)
    instruction['lanes'] = lanes

  return instruction
