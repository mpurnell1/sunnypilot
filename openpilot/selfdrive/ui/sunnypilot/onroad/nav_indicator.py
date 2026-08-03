"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from math import cos, radians, sin

import pyray as rl

from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG
from openpilot.selfdrive.ui.sunnypilot.nav_status import NavState, NavStatus
from openpilot.sunnypilot.navd.helpers import ROUNDABOUT_TYPES
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

# The set speed box starts at x+60, y+45 and is set_speed_height tall; the speed limit signs
# stack in the column to its right. This drops into the empty space directly below it.
LEFT_MARGIN = 60
TOP_OFFSET = 45 + UI_CONFIG.set_speed_height + 20

BOX_HEIGHT = 84
BOX_GAP = 20
ICON_WIDTH = 44
ICON_GAP = 26

# double height so the maneuver icon and the distance stack instead of sharing a row the
# column is too narrow for
TURN_BOX_HEIGHT = BOX_HEIGHT * 2
TURN_ICON_WIDTH = 64
TURN_ICON_CY = 60
TURN_TEXT_TOP = 108
TURN_FONT_SIZE = 36

# extra card row for lane guidance, one small arrow per lane
LANE_ROW_HEIGHT = 56
LANE_ICON_SIZE = 28
LANE_GAP = 10
LANE_INACTIVE = rl.Color(255, 255, 255, 80)

BACKGROUND = rl.Color(0, 0, 0, 140)
GOOD = rl.Color(0x2f, 0xc4, 0x6e, 0xff)
BAD = rl.Color(0xf2, 0x4b, 0x4b, 0xff)
# opaque, so the overlaps the glyphs are built from never double-blend into seams
TURN_COLOR = rl.Color(255, 255, 255, 255)
# the road not taken: exits and forks draw the continuing carriageway too, so the bright
# branch reads as a path through a junction rather than a floating arrow
ROAD_DIM = rl.Color(255, 255, 255, 80)

METERS_PER_FOOT = 0.3048
METERS_PER_MILE = 1609.344

# degrees clockwise from straight ahead. 'uturn' and roundabout maneuvers are absent by
# design: they get dedicated glyphs rather than a rotated arrow.
ARROW_ANGLES = {
  'straight': 0, 'none': 0,
  'slightRight': 45, 'right': 90, 'sharpRight': 135,
  'slightLeft': -45, 'left': -90, 'sharpLeft': -135,
}


def format_distance(distance_m: float, is_metric: bool) -> str:
  distance_m = max(0.0, distance_m)
  if is_metric:
    if distance_m < 1000:
      return f"{round(distance_m / 10) * 10:.0f} m"
    km = distance_m / 1000
    return f"{km:.0f} km" if km >= 10 else f"{km:.1f} km"
  feet = distance_m / METERS_PER_FOOT
  if feet < 1000:
    return f"{round(feet / 50) * 50:.0f} ft"
  miles = distance_m / METERS_PER_MILE
  return f"{miles:.0f} mi" if miles >= 10 else f"{miles:.1f} mi"


# allManeuvers[0] is the step being driven, whose maneuver is already behind the car; the turn
# that lies ahead is the second entry. Near the destination the 'arrive' step can be the only
# one left, and that one is still worth showing.
def pick_upcoming_maneuver(maneuvers) -> tuple[str, str, float] | None:
  if len(maneuvers) > 1:
    m = maneuvers[1]
  elif len(maneuvers) == 1 and maneuvers[0].type == 'arrive':
    m = maneuvers[0]
  else:
    return None
  return m.type, m.modifier, m.distance


def _draw_pin(cx: float, cy: float, color: rl.Color) -> None:
  radius = ICON_WIDTH / 2
  head_y = cy - radius * 0.35

  # draw_poly handles vertex winding, unlike draw_triangle
  rl.draw_poly(rl.Vector2(cx, head_y + radius * 0.75), 3, radius * 0.95, 90, color)
  rl.draw_circle(int(cx), int(head_y), radius, color)
  rl.draw_circle(int(cx), int(head_y), radius * 0.38, BACKGROUND)


def _draw_flag(cx: float, cy: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  pole_w = max(3.0, size * 0.12)
  height = size * 1.05
  x = cx - size / 2
  top = cy - height / 2

  rl.draw_rectangle_rec(rl.Rectangle(x, top, pole_w, height), color)
  rl.draw_rectangle_rec(rl.Rectangle(x + pole_w, top, size - pole_w, height * 0.45), color)


# glyphs are composed of strokes that meet flush instead of overlapping: the card colors are
# translucent in places (inactive lanes, dim branches), and stacked translucent primitives
# double-blend into visible blotches
def _stroke(x: float, y: float, heading_deg: float, length: float, half_stroke: float, color: rl.Color) -> None:
  # draw_rectangle_pro rotates about the origin point placed at rec.x/rec.y; the rectangle's
  # local +y axis points down unrotated, so heading + 180 sends it along the travel direction
  rl.draw_rectangle_pro(rl.Rectangle(x, y, 2 * half_stroke, length),
                        rl.Vector2(half_stroke, 0), heading_deg + 180, color)


def _cap(x: float, y: float, heading_deg: float, half_stroke: float, color: rl.Color) -> None:
  # a half-disc butted against the stroke's end, so nothing is painted twice
  rl.draw_circle_sector(rl.Vector2(x, y), half_stroke, heading_deg - 180, heading_deg, 16, color)


def _head(x: float, y: float, heading_deg: float, radius: float, color: rl.Color) -> tuple[float, float]:
  """Arrowhead whose flat back sits exactly on (x, y); returns the direction unit vector."""
  rad = radians(heading_deg)
  dx, dy = sin(rad), -cos(rad)
  # a poly vertex sits at `rotation` degrees from +x, so aim one vertex along the direction;
  # the triangle's back edge is half a radius behind its center
  rl.draw_poly(rl.Vector2(x + dx * radius * 0.5, y + dy * radius * 0.5), 3, radius, heading_deg - 90, color)
  return dx, dy


def _draw_arrow(cx: float, cy: float, angle_deg: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  half = size / 2
  head_radius = size * 0.36
  half_stroke = size * 0.11
  # screen y grows downward, so straight ahead is (0, -1)
  rad = radians(angle_deg)
  dx, dy = sin(rad), -cos(rad)

  shaft_len = size - head_radius * 1.5
  _stroke(cx - dx * half, cy - dy * half, angle_deg, shaft_len, half_stroke, color)
  _head(cx + dx * (half - head_radius * 1.5), cy + dy * (half - head_radius * 1.5), angle_deg, head_radius, color)


# every turn-card glyph follows the u-turn's convention: the stem entering from the bottom
# is the car's current direction of travel, and the arrowhead leaves along the maneuver
def _draw_turn(cx: float, cy: float, angle_deg: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  if angle_deg == 0:
    _draw_arrow(cx, cy, 0, color, size)
    return

  half_stroke = size * 0.11
  a = abs(angle_deg)
  sgn = 1.0 if angle_deg > 0 else -1.0
  # the corner is a real ring segment, like the u-turn's arc, so the elbow is smooth by
  # construction; sharper turns bend earlier and reach back down
  arc_radius = {45: 0.20, 90: 0.20, 135: 0.16}[a] * size
  stem_x = cx - sgn * {45: 0.18, 90: 0.28, 135: 0.22}[a] * size
  arc_y = cy + {45: 0.10, 90: -0.06, 135: -0.16}[a] * size
  leg_len = {45: 0.30, 90: 0.22, 135: 0.28}[a] * size

  rl.draw_rectangle_rec(rl.Rectangle(stem_x - half_stroke, arc_y, 2 * half_stroke, cy + size * 0.5 - arc_y), color)
  center = rl.Vector2(stem_x + sgn * arc_radius, arc_y)
  # ring angles run clockwise from +x; the stem joins the ring where its tangent is vertical
  if sgn > 0:
    rl.draw_ring(center, arc_radius - half_stroke, arc_radius + half_stroke, 180, 180 + a, 24, color)
  else:
    rl.draw_ring(center, arc_radius - half_stroke, arc_radius + half_stroke, -a, 0, 24, color)

  rad = radians(a)
  leg_x = stem_x + sgn * arc_radius * (1 - cos(rad))
  leg_y = arc_y - arc_radius * sin(rad)
  _stroke(leg_x, leg_y, angle_deg, leg_len, half_stroke, color)
  head_radius = size * 0.22
  dx, dy = sin(radians(angle_deg)), -cos(radians(angle_deg))
  _head(leg_x + dx * leg_len, leg_y + dy * leg_len, angle_deg, head_radius, color)


# exits keep the continuing carriageway vertical; a plain fork leans both branches apart
def _draw_fork(cx: float, cy: float, sgn: float, color: rl.Color, size: float = ICON_WIDTH, exit_ramp: bool = False) -> None:
  half_stroke = size * 0.11
  split_y = cy + size * 0.04
  branch_len = size * 0.52

  # the dim carriageway goes down first so the bright path always paints over it
  through_angle = 0.0 if exit_ramp else -sgn * 25.0
  through_rad = radians(through_angle)
  _stroke(cx, split_y, through_angle, branch_len, half_stroke, ROAD_DIM)
  _cap(cx + sin(through_rad) * branch_len, split_y - cos(through_rad) * branch_len, through_angle, half_stroke, ROAD_DIM)

  rl.draw_rectangle_rec(rl.Rectangle(cx - half_stroke, split_y, 2 * half_stroke, cy + size * 0.5 - split_y), color)
  rl.draw_circle_v(rl.Vector2(cx, split_y), half_stroke, color)

  taken_angle = sgn * 35.0
  rad = radians(taken_angle)
  dx, dy = sin(rad), -cos(rad)
  _stroke(cx, split_y, taken_angle, branch_len, half_stroke, color)
  _head(cx + dx * branch_len, split_y + dy * branch_len, taken_angle, size * 0.22, color)


# the dim leg is the carriageway being joined, running up to the join from below; the bright
# path comes in from the ramp side, bends through a ring segment, and continues along it
def _draw_merge(cx: float, cy: float, sgn: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  half_stroke = size * 0.11
  ramp_heading = sgn * 38.0
  arc_radius = size * 0.20
  highway_x = cx + sgn * size * 0.18
  arc_y = cy + size * 0.04
  top_y = cy - size * 0.36
  bottom = cy + size * 0.5

  rl.draw_rectangle_rec(rl.Rectangle(highway_x - half_stroke, arc_y, 2 * half_stroke, bottom - arc_y), ROAD_DIM)

  # the ramp straightens into the carriageway through a ring segment whose tangent is
  # vertical at the join, so there is no corner to poke past either stroke
  center = rl.Vector2(highway_x - sgn * arc_radius, arc_y)
  rad = radians(abs(ramp_heading))
  if sgn > 0:
    rl.draw_ring(center, arc_radius - half_stroke, arc_radius + half_stroke, 0, abs(ramp_heading), 20, color)
    arc_end = rl.Vector2(center.x + arc_radius * cos(rad), arc_y + arc_radius * sin(rad))
  else:
    rl.draw_ring(center, arc_radius - half_stroke, arc_radius + half_stroke, 180 - abs(ramp_heading), 180, 20, color)
    arc_end = rl.Vector2(center.x - arc_radius * cos(rad), arc_y + arc_radius * sin(rad))

  ramp_len = (bottom - arc_end.y) / cos(rad)
  _stroke(arc_end.x, arc_end.y, ramp_heading + 180, ramp_len, half_stroke, color)
  _cap(arc_end.x - sin(radians(ramp_heading)) * ramp_len, arc_end.y + cos(rad) * ramp_len,
       ramp_heading + 180, half_stroke, color)

  rl.draw_rectangle_rec(rl.Rectangle(highway_x - half_stroke, top_y, 2 * half_stroke, arc_y - top_y), color)
  _head(highway_x, top_y, 0, size * 0.22, color)


def _draw_uturn(cx: float, cy: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  radius = size * 0.30
  half_stroke = size * 0.11
  arc_cy = cy - size * 0.12
  # ring angles run clockwise from +x in screen coords, so 180..360 is the upper half
  rl.draw_ring(rl.Vector2(cx, arc_cy), radius - half_stroke, radius + half_stroke, 180, 360, 24, color)

  # approach leg up the right side, exit leg down the left ending in the arrowhead. Flush
  # joints rather than overlaps: a translucent u-turn (inactive lane) must not double-blend
  leg_len = size * 0.46
  rl.draw_rectangle_rec(rl.Rectangle(cx + radius - half_stroke, arc_cy, 2 * half_stroke, leg_len), color)
  rl.draw_rectangle_rec(rl.Rectangle(cx - radius - half_stroke, arc_cy, 2 * half_stroke, leg_len * 0.5), color)
  _head(cx - radius, arc_cy + leg_len * 0.5, 180, size * 0.24, color)


def _draw_roundabout(cx: float, cy: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  radius = size * 0.26
  half_stroke = size * 0.10
  # the ring sits high enough that the entry stem gets a real run from the glyph bottom
  ring_cy = cy
  rl.draw_ring(rl.Vector2(cx, ring_cy), radius - half_stroke, radius + half_stroke, 0, 360, 32, color)

  # generic glyph: enter from below, arrow out the top; banner text carries the exact exit
  rl.draw_rectangle_rec(rl.Rectangle(cx - half_stroke, ring_cy + radius - half_stroke, 2 * half_stroke,
                                     cy + size * 0.5 - (ring_cy + radius - half_stroke)), color)
  exit_h = size * 0.14
  rl.draw_rectangle_rec(rl.Rectangle(cx - half_stroke, ring_cy - radius - exit_h, 2 * half_stroke, exit_h + half_stroke), color)
  head_radius = size * 0.22
  rl.draw_poly(rl.Vector2(cx, ring_cy - radius - exit_h - head_radius * 0.4), 3, head_radius, -90, color)


# pin is the GPS fix, flag is the route. The flag needs a destination to mean anything, but the
# pin does not, so it stays visible on its own.
class NavIndicatorRenderer:
  def __init__(self):
    self.nav_status = NavStatus()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    # where the card stack ends this frame, so the route summary below can stay clear of it
    self.stack_bottom: float = 0.0

  def update(self) -> None:
    self.nav_status.update()

  def _render_status_icons(self, box: rl.Rectangle, show_pin: bool, show_flag: bool) -> None:
    status = self.nav_status
    rl.draw_rectangle_rounded(box, 0.35, 10, BACKGROUND)

    cy = box.y + BOX_HEIGHT / 2
    span = ICON_WIDTH * (show_pin + show_flag) + ICON_GAP * (show_pin and show_flag)
    x = box.x + (box.width - span) / 2 + ICON_WIDTH / 2

    if show_pin:
      _draw_pin(x, cy, GOOD if status.gps_locked else BAD)
      x += ICON_WIDTH + ICON_GAP
    if show_flag:
      _draw_flag(x, cy, GOOD if status.state == NavState.ACTIVE else BAD)

  def _render_lanes(self, box: rl.Rectangle, lanes) -> None:
    n = len(lanes)
    size = min(LANE_ICON_SIZE, (box.width - 24 - (n - 1) * LANE_GAP) / n)
    span = size * n + LANE_GAP * (n - 1)
    cy = box.y + box.height - LANE_ROW_HEIGHT / 2
    x = box.x + (box.width - span) / 2 + size / 2

    for lane in lanes:
      # a lane can serve several directions; when it's the one to take, show the direction
      # the maneuver uses, otherwise its first listed direction
      direction = lane.activeDirection if lane.active and lane.activeDirection else \
        (lane.directions[0] if len(lane.directions) else 'straight')
      color = TURN_COLOR if lane.active else LANE_INACTIVE
      if direction == 'uturn':
        _draw_uturn(x, cy, color, size)
      else:
        # same elbow glyphs as the turn card, so the lane row matches overhead lane signage
        _draw_turn(x, cy, ARROW_ANGLES.get(direction, 0), color, size)
      x += size + LANE_GAP

  def _render_turn(self, box: rl.Rectangle, maneuver: tuple[str, str, float], lanes) -> None:
    maneuver_type, modifier, distance = maneuver
    # roundness is a fraction of the box's short side, so halve it to keep the same corner
    # radius as the single-height boxes
    rl.draw_rectangle_rounded(box, 0.175, 10, BACKGROUND)

    # icon above, distance below, both centered
    icon_cx = box.x + box.width / 2
    icon_cy = box.y + TURN_ICON_CY

    angle = ARROW_ANGLES.get(modifier, 0)
    if maneuver_type == 'arrive':
      _draw_flag(icon_cx, icon_cy, TURN_COLOR, TURN_ICON_WIDTH)
    elif any(t in maneuver_type for t in ROUNDABOUT_TYPES):
      _draw_roundabout(icon_cx, icon_cy, TURN_COLOR, TURN_ICON_WIDTH)
    elif modifier == 'uturn':
      _draw_uturn(icon_cx, icon_cy, TURN_COLOR, TURN_ICON_WIDTH)
    # a sideless fork or merge has no branch to brighten, so those fall through to the arrow
    elif maneuver_type in ('off ramp', 'fork') and angle != 0:
      _draw_fork(icon_cx, icon_cy, 1.0 if angle > 0 else -1.0, TURN_COLOR, TURN_ICON_WIDTH, exit_ramp=maneuver_type == 'off ramp')
    elif maneuver_type == 'merge' and angle != 0:
      _draw_merge(icon_cx, icon_cy, 1.0 if angle > 0 else -1.0, TURN_COLOR, TURN_ICON_WIDTH)
    else:
      _draw_turn(icon_cx, icon_cy, angle, TURN_COLOR, TURN_ICON_WIDTH)

    text = format_distance(distance, ui_state.is_metric)
    text_size = measure_text_cached(self._font, text, TURN_FONT_SIZE)
    origin = rl.Vector2(icon_cx - text_size.x / 2, box.y + TURN_TEXT_TOP)
    rl.draw_text_ex(self._font, text, origin, TURN_FONT_SIZE, 0, TURN_COLOR)

    if len(lanes):
      self._render_lanes(box, lanes)

  def render(self, rect: rl.Rectangle) -> None:
    status = self.nav_status
    self.stack_bottom = rect.y + TOP_OFFSET
    # hidden unless navigation is opted into and navigationd is actually publishing
    if not status.allow_navigation or status.state == NavState.OFFLINE:
      return

    # each element is individually switchable from Settings > Navigation
    show_pin = status.show_gps_icon
    show_flag = status.show_route_icon and status.state != NavState.NO_DESTINATION
    maneuver = None
    lanes = []
    if status.show_turn_indicator and status.state == NavState.ACTIVE:
      maneuver = pick_upcoming_maneuver(ui_state.sm['navigationd'].allManeuvers)
      # double-gated: navigationd only publishes lanes while NavLaneGuidance is on, and the
      # card only grows for them while it is, so a stale message can't resize the HUD
      if maneuver is not None and status.lane_guidance:
        lanes = ui_state.sm['navigationd'].lanes

    if not (show_pin or show_flag) and maneuver is None:
      return

    width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    top = rect.y + TOP_OFFSET

    # the turn readout takes the status icons' slot when both of them are switched off,
    # and stacks directly beneath them otherwise
    if show_pin or show_flag:
      self._render_status_icons(rl.Rectangle(rect.x + LEFT_MARGIN, top, width, BOX_HEIGHT), show_pin, show_flag)
      top += BOX_HEIGHT + BOX_GAP

    if maneuver is not None:
      height = TURN_BOX_HEIGHT + (LANE_ROW_HEIGHT if len(lanes) else 0)
      self._render_turn(rl.Rectangle(rect.x + LEFT_MARGIN, top, width, height), maneuver, lanes)
      top += height
    self.stack_bottom = top
