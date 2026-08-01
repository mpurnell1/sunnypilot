"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from math import radians, sin, cos

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

BACKGROUND = rl.Color(0, 0, 0, 140)
GOOD = rl.Color(0x2f, 0xc4, 0x6e, 0xff)
BAD = rl.Color(0xf2, 0x4b, 0x4b, 0xff)
TURN_COLOR = rl.Color(255, 255, 255, 230)

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


def _draw_arrow(cx: float, cy: float, angle_deg: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  half = size / 2
  head_radius = size * 0.36
  shaft_w = size * 0.22
  # screen y grows downward, so straight ahead is (0, -1)
  rad = radians(angle_deg)
  dx, dy = sin(rad), -cos(rad)

  tail = rl.Vector2(cx - dx * half, cy - dy * half)
  # draw_rectangle_pro rotates about the origin point placed at rec.x/rec.y; the rectangle's
  # local +y axis points down unrotated, so angle + 180 sends it along the travel direction
  shaft_len = size - head_radius
  rl.draw_rectangle_pro(rl.Rectangle(tail.x, tail.y, shaft_w, shaft_len),
                        rl.Vector2(shaft_w / 2, 0), angle_deg + 180, color)

  # a poly vertex sits at `rotation` degrees from +x, so aim one vertex along the direction
  head_center = rl.Vector2(cx + dx * (half - head_radius), cy + dy * (half - head_radius))
  rl.draw_poly(head_center, 3, head_radius, angle_deg - 90, color)


def _draw_uturn(cx: float, cy: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  radius = size * 0.30
  half_stroke = size * 0.11
  arc_cy = cy - size * 0.12
  # ring angles run clockwise from +x in screen coords, so 180..360 is the upper half
  rl.draw_ring(rl.Vector2(cx, arc_cy), radius - half_stroke, radius + half_stroke, 180, 360, 24, color)

  # approach leg up the right side, exit leg down the left ending in the arrowhead
  leg_len = size * 0.46
  rl.draw_rectangle_rec(rl.Rectangle(cx + radius - half_stroke, arc_cy, 2 * half_stroke, leg_len), color)
  rl.draw_rectangle_rec(rl.Rectangle(cx - radius - half_stroke, arc_cy, 2 * half_stroke, leg_len * 0.5), color)
  head_radius = size * 0.24
  rl.draw_poly(rl.Vector2(cx - radius, arc_cy + leg_len * 0.5 + head_radius * 0.4), 3, head_radius, 90, color)


def _draw_roundabout(cx: float, cy: float, color: rl.Color, size: float = ICON_WIDTH) -> None:
  radius = size * 0.28
  half_stroke = size * 0.10
  ring_cy = cy + size * 0.08
  rl.draw_ring(rl.Vector2(cx, ring_cy), radius - half_stroke, radius + half_stroke, 0, 360, 32, color)

  # generic glyph: enter from below, arrow out the top; banner text carries the exact exit
  stem_h = size * 0.20
  rl.draw_rectangle_rec(rl.Rectangle(cx - half_stroke, ring_cy + radius - half_stroke, 2 * half_stroke, stem_h), color)
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

  def _render_turn(self, box: rl.Rectangle, maneuver: tuple[str, str, float]) -> None:
    maneuver_type, modifier, distance = maneuver
    # roundness is a fraction of the box's short side, so halve it to keep the same corner
    # radius as the single-height boxes
    rl.draw_rectangle_rounded(box, 0.175, 10, BACKGROUND)

    # icon above, distance below, both centered
    icon_cx = box.x + box.width / 2
    icon_cy = box.y + TURN_ICON_CY

    if maneuver_type == 'arrive':
      _draw_flag(icon_cx, icon_cy, TURN_COLOR, TURN_ICON_WIDTH)
    elif any(t in maneuver_type for t in ROUNDABOUT_TYPES):
      _draw_roundabout(icon_cx, icon_cy, TURN_COLOR, TURN_ICON_WIDTH)
    elif modifier == 'uturn':
      _draw_uturn(icon_cx, icon_cy, TURN_COLOR, TURN_ICON_WIDTH)
    else:
      _draw_arrow(icon_cx, icon_cy, ARROW_ANGLES.get(modifier, 0), TURN_COLOR, TURN_ICON_WIDTH)

    text = format_distance(distance, ui_state.is_metric)
    text_size = measure_text_cached(self._font, text, TURN_FONT_SIZE)
    origin = rl.Vector2(icon_cx - text_size.x / 2, box.y + TURN_TEXT_TOP)
    rl.draw_text_ex(self._font, text, origin, TURN_FONT_SIZE, 0, TURN_COLOR)

  def render(self, rect: rl.Rectangle) -> None:
    status = self.nav_status
    # hidden unless navigation is opted into and navigationd is actually publishing
    if not status.allow_navigation or status.state == NavState.OFFLINE:
      return

    # each element is individually switchable from Settings > Navigation
    show_pin = status.show_gps_icon
    show_flag = status.show_route_icon and status.state != NavState.NO_DESTINATION
    maneuver = None
    if status.show_turn_indicator and status.state == NavState.ACTIVE:
      maneuver = pick_upcoming_maneuver(ui_state.sm['navigationd'].allManeuvers)

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
      self._render_turn(rl.Rectangle(rect.x + LEFT_MARGIN, top, width, TURN_BOX_HEIGHT), maneuver)
