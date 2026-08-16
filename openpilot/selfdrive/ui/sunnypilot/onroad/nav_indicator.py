"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from math import cos, radians, sin

import pyray as rl

from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG
from openpilot.selfdrive.ui.sunnypilot.nav_status import ROUTE_FAILURE_THRESHOLD, NavStatus
from openpilot.selfdrive.ui.sunnypilot.onroad.transient_nav import (
  ChipMode, TransientNav, TransientNavState, chip_mode, flag_raised, pick_upcoming_maneuver,
)
from openpilot.sunnypilot.navd.helpers import ROUNDABOUT_TYPES
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# The set speed box starts at x+60, y+45 and is set_speed_height tall; the speed limit signs
# stack in the column to its right. This drops into the empty space directly below it.
LEFT_MARGIN = 60
TOP_OFFSET = 45 + UI_CONFIG.set_speed_height + 20

BOX_HEIGHT = 84
ICON_WIDTH = 44

# the quiet skin is a hint, not a card: a small glyph and the distance share the single-height
# chip, and the content is dimmed so the road keeps visual priority
CHIP_ICON_SIZE = 40
CHIP_FONT_SIZE = 32
CHIP_GAP = 14
CHIP_DIM = rl.Color(255, 255, 255, 170)
CHIP_SEARCHING = rl.Color(255, 255, 255, 110)

LANE_INACTIVE = rl.Color(255, 255, 255, 80)

BACKGROUND = rl.Color(0, 0, 0, 140)
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


# a lane can serve several directions; when it's the one to take, show the direction the
# maneuver uses, otherwise its first listed direction
def lane_direction(lane) -> str:
  if lane.active and lane.activeDirection:
    return lane.activeDirection
  return lane.directions[0] if len(lane.directions) else 'straight'


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


def _draw_flag(cx: float, cy: float, color: rl.Color, size: float = ICON_WIDTH, banner: bool = True) -> None:
  """The destination flag; the not-yet stage of the searching progression is an empty
  flagpole, ball finial on top and a base plinth under it, so a destination with no GPS
  fix reads as a planted pole rather than a stray vertical bar."""
  pole_w = max(3.0, size * 0.12)
  height = size * 1.05
  x = cx - size / 2
  top = cy - height / 2

  if banner:
    rl.draw_rectangle_rec(rl.Rectangle(x, top, pole_w, height), color)
    rl.draw_rectangle_rec(rl.Rectangle(x + pole_w, top, size - pole_w, height * 0.45), color)
    return

  # flush joints, no overlaps: the searching color is translucent and stacked primitives
  # double-blend into bright seams. The ball butts the pole at its tangent, which leaves
  # the natural waist a real finial has; the base butts the pole's foot.
  ball_r = size * 0.14
  base_h = max(3.0, size * 0.10)
  base_w = size * 0.52
  pole_cx = x + pole_w / 2
  rl.draw_circle_v(rl.Vector2(pole_cx, top + ball_r), ball_r, color)
  rl.draw_rectangle_rec(rl.Rectangle(x, top + 2 * ball_r, pole_w, height - 2 * ball_r - base_h), color)
  rl.draw_rectangle_rec(rl.Rectangle(pole_cx - base_w / 2, top + height - base_h, base_w, base_h), color)


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
  """Isoceles arrowhead, longer than it is wide, whose back edge overlaps the stroke it
  caps by a hair so the joint never shows. The overlap is safe because glyphs composite
  opaque inside the texture cache; only draw at full opacity outside it."""
  rad = radians(heading_deg)
  dx, dy = sin(rad), -cos(rad)
  px, py = -dy, dx
  head_len = radius * 1.5
  half_w = radius * 0.85
  bx, by = x - dx * radius * 0.1, y - dy * radius * 0.1
  tip = rl.Vector2(bx + dx * head_len, by + dy * head_len)
  left = rl.Vector2(bx + px * half_w, by + py * half_w)
  right = rl.Vector2(bx - px * half_w, by - py * half_w)
  # winding decides visibility in rlgl; drawing both keeps every heading covered, and
  # inside the opaque cache the second pass changes nothing
  rl.draw_triangle(tip, left, right, color)
  rl.draw_triangle(tip, right, left, color)
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
  _head(cx, ring_cy - radius - exit_h, 0, size * 0.22, color)


def _draw_maneuver_shapes(cx: float, cy: float, maneuver_type: str, modifier: str, color: rl.Color, size: float) -> None:
  angle = ARROW_ANGLES.get(modifier, 0)
  if maneuver_type == 'arrive':
    _draw_flag(cx, cy, color, size)
  elif any(t in maneuver_type for t in ROUNDABOUT_TYPES):
    _draw_roundabout(cx, cy, color, size)
  elif modifier == 'uturn':
    _draw_uturn(cx, cy, color, size)
  # a sideless fork or merge has no branch to brighten, so those fall through to the arrow
  elif maneuver_type in ('off ramp', 'fork') and angle != 0:
    _draw_fork(cx, cy, 1.0 if angle > 0 else -1.0, color, size, exit_ramp=maneuver_type == 'off ramp')
  elif maneuver_type == 'merge' and angle != 0:
    _draw_merge(cx, cy, 1.0 if angle > 0 else -1.0, color, size)
  else:
    _draw_turn(cx, cy, angle, color, size)


# --- the supersampled glyph cache ---
# Two problems end at the same place. The device grants no MSAA, so rotated primitives
# stair-step at chip size; and translucent glyph colors forbid overlapped joints, which
# forces butt joints that show notches wherever the per-angle constants miss. So each
# glyph is drawn once, opaque and at GLYPH_SS times its size, into a cached
# RenderTexture, and blitted scaled down with bilinear filtering. The caller's color,
# alpha included, tints the whole texture at blit time: edges get real antialiasing,
# joints may overlap freely, and internal translucency (the dim road branches) dims the
# glyph as one piece instead of double-blending.
GLYPH_SS = 4
_GLYPH_PAD = 1.5  # glyphs poke past their size box (the flag's height, the roundabout's arrow)
_glyph_cache: dict[tuple, rl.RenderTexture] = {}


def _glyph_texture(key: tuple, draw, side: int) -> rl.RenderTexture:
  tex = _glyph_cache.get(key)
  if tex is None:
    tex = rl.load_render_texture(side, side)
    rl.begin_texture_mode(tex)
    rl.clear_background(rl.BLANK)
    draw()
    rl.end_texture_mode()
    rl.set_texture_filter(tex.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
    _glyph_cache[key] = tex
  return tex


def _blit_glyph(tex: rl.RenderTexture, cx: float, cy: float, color: rl.Color, dest_side: float) -> None:
  # drawing over a transparent ground leaves the texture premultiplied, so the blit uses
  # the premultiply blend with the tint folded into the color channels
  tint = rl.Color(color.r * color.a // 255, color.g * color.a // 255, color.b * color.a // 255, color.a)
  src = rl.Rectangle(0, 0, tex.texture.width, -tex.texture.height)
  dest = rl.Rectangle(cx - dest_side / 2, cy - dest_side / 2, dest_side, dest_side)
  rl.begin_blend_mode(rl.BlendMode.BLEND_ALPHA_PREMULTIPLY)
  rl.draw_texture_pro(tex.texture, src, dest, rl.Vector2(0, 0), 0, tint)
  rl.end_blend_mode()


def _draw_maneuver_icon(cx: float, cy: float, maneuver_type: str, modifier: str, color: rl.Color, size: float) -> None:
  side = int(size * GLYPH_SS * _GLYPH_PAD)
  key = (maneuver_type, modifier, side)
  tex = _glyph_texture(key, lambda: _draw_maneuver_shapes(side / 2, side / 2, maneuver_type, modifier,
                                                          TURN_COLOR, size * GLYPH_SS), side)
  _blit_glyph(tex, cx, cy, color, size * _GLYPH_PAD)


def draw_lane_glyph(cx: float, cy: float, direction: str, color: rl.Color, size: float) -> None:
  """The lane rows' entry point, so their tiny icons share the cache's antialiasing."""
  side = int(size * GLYPH_SS * _GLYPH_PAD)
  key = ('lane', direction, side)

  def _draw():
    if direction == 'uturn':
      _draw_uturn(side / 2, side / 2, TURN_COLOR, size * GLYPH_SS)
    else:
      _draw_turn(side / 2, side / 2, ARROW_ANGLES.get(direction, 0), TURN_COLOR, size * GLYPH_SS)

  _blit_glyph(_glyph_texture(key, _draw, side), cx, cy, color, size * _GLYPH_PAD)


# The transient rail: the quiet chip hints at the next maneuver and doubles as the status
# indicator; when the TransientNav machine expands, the top-center banner (nav_banner) takes
# over and the rail stays clear. The widget's rect is set to exactly what was drawn, so the
# touch target and the visible chip can never disagree.
class NavIndicatorRenderer(Widget):
  def __init__(self):
    super().__init__()
    self.nav_status = NavStatus()
    self.transient = TransientNav()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    self._mode = ChipMode.HIDDEN
    self._update_frame = -1
    # where the card stack ends this frame, so the route summary below can stay clear of it
    self.stack_bottom: float = 0.0

  @property
  def mode(self) -> ChipMode:
    return self._mode

  def update_state(self) -> None:
    """Advance the nav state once per SubMaster frame; the HUD calls this before its own
    layout so the banner and speed pill reflow on the same frame the machine expands."""
    if self._update_frame == ui_state.sm.frame:
      return
    self._update_frame = ui_state.sm.frame
    self.nav_status.update()
    self._mode = chip_mode(self.nav_status)
    msg = ui_state.sm['navigationd']
    self.transient.update(self._mode == ChipMode.LIVE, msg.allManeuvers, msg.audioCueId, msg.audioCueStage,
                          off_route=msg.routeState != 'onRoute')

  def _update_state(self) -> None:
    self.update_state()

  def _handle_mouse_release(self, mouse_pos) -> None:
    super()._handle_mouse_release(mouse_pos)
    self.transient.on_tap()

  def _render_status_chip(self, box: rl.Rectangle, color: rl.Color, banner: bool = True) -> None:
    # no route yet: the flag that will mark the destination, dimmed while searching and
    # in the failure color once route requests are actually failing; while searching the
    # flag raises in stages, a bare pole until the GPS fix comes in
    rl.draw_rectangle_rounded(box, 0.35, 10, BACKGROUND)
    _draw_flag(box.x + box.width / 2, box.y + box.height / 2, color, CHIP_ICON_SIZE, banner=banner)

  def _render_chip(self, box: rl.Rectangle, maneuver: tuple[str, str, float], dimmed: bool = False) -> None:
    maneuver_type, modifier, distance = maneuver
    rl.draw_rectangle_rounded(box, 0.35, 10, BACKGROUND)

    if dimmed:
      # off route: the glyph dims and the distance drops, because that number counts down
      # to a maneuver the car is no longer approaching
      _draw_maneuver_icon(box.x + box.width / 2, box.y + box.height / 2,
                          maneuver_type, modifier, CHIP_SEARCHING, CHIP_ICON_SIZE)
      return

    text = format_distance(distance, ui_state.is_metric)
    text_size = measure_text_cached(self._font, text, CHIP_FONT_SIZE)
    span = CHIP_ICON_SIZE + CHIP_GAP + text_size.x
    x = box.x + (box.width - span) / 2
    cy = box.y + box.height / 2
    _draw_maneuver_icon(x + CHIP_ICON_SIZE / 2, cy, maneuver_type, modifier, CHIP_DIM, CHIP_ICON_SIZE)
    rl.draw_text_ex(self._font, text, rl.Vector2(x + CHIP_ICON_SIZE + CHIP_GAP, cy - text_size.y / 2),
                    CHIP_FONT_SIZE, 0, CHIP_DIM)

  def _render(self, rect: rl.Rectangle) -> None:
    top = rect.y + TOP_OFFSET
    self.stack_bottom = top
    # nothing drawn means nothing to tap; a real target is set below once a box is drawn
    self.set_rect(rl.Rectangle(0, 0, 0, 0))
    if self._mode == ChipMode.HIDDEN:
      return

    width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + LEFT_MARGIN

    if self._mode in (ChipMode.SEARCHING, ChipMode.FAILURE):
      # informational only, so it never swallows a tap meant for the road view
      searching = self._mode == ChipMode.SEARCHING
      self._render_status_chip(rl.Rectangle(x, top, width, BOX_HEIGHT),
                               CHIP_SEARCHING if searching else BAD,
                               banner=not searching or flag_raised(self.nav_status.state))
      self.stack_bottom = top + BOX_HEIGHT
      return

    if self.transient.state in (TransientNavState.APPROACH, TransientNavState.PINNED):
      # the top-center banner is the expanded skin; the rail stays clear while it is up
      return

    msg = ui_state.sm['navigationd']
    if msg.routeState == 'rerouting':
      # the searching flag is the reroute cue's visual: the route being counted against is
      # being replaced. Failing recompute requests turn it red, like any other failure.
      failing = msg.routeFailures >= ROUTE_FAILURE_THRESHOLD
      self._render_status_chip(rl.Rectangle(x, top, width, BOX_HEIGHT), BAD if failing else CHIP_SEARCHING)
      self.stack_bottom = top + BOX_HEIGHT
      return

    maneuver = pick_upcoming_maneuver(msg.allManeuvers)
    if maneuver is None:
      return

    box = rl.Rectangle(x, top, width, BOX_HEIGHT)
    self._render_chip(box, maneuver, dimmed=msg.routeState == 'offRoute')
    self.stack_bottom = top + box.height
    self.set_rect(box)
