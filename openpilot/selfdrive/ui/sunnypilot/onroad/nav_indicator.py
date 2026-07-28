"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.selfdrive.ui.sunnypilot.nav_status import NavState, NavStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached

FONT_SIZE = 38
PILL_HEIGHT = 56
PILL_PADDING = 24
ICON_WIDTH = 30
ICON_GAP = 14
# sits directly under the road name, which occupies the top 60px of the camera view
TOP_OFFSET = 64
MAX_PILL_WIDTH_RATIO = 0.5

BACKGROUND = rl.Color(0, 0, 0, 140)
GPS_LOCKED = rl.Color(0x2f, 0xc4, 0x6e, 0xff)
GPS_WAITING = rl.Color(0xff, 0xb8, 0x2e, 0xff)
ROUTE_FAILED = rl.Color(0xf2, 0x6d, 0x6d, 0xff)
TEXT_COLOR = rl.Color(255, 255, 255, 220)


def _draw_pin(x: float, cy: float, color: rl.Color) -> None:
  """A map pin: round head with a hole, tapering to a point below."""
  cx = x + ICON_WIDTH / 2
  radius = ICON_WIDTH / 2
  head_y = cy - radius * 0.35

  # draw_poly handles vertex winding, unlike draw_triangle
  rl.draw_poly(rl.Vector2(cx, head_y + radius * 0.75), 3, radius * 0.95, 90, color)
  rl.draw_circle(int(cx), int(head_y), radius, color)
  rl.draw_circle(int(cx), int(head_y), radius * 0.38, BACKGROUND)


def _draw_flag(x: float, cy: float, color: rl.Color) -> None:
  """A destination flag: pole on the left, pennant filling the top half."""
  pole_w = max(2.0, ICON_WIDTH * 0.11)
  height = ICON_WIDTH * 1.05
  top = cy - height / 2

  rl.draw_rectangle_rec(rl.Rectangle(x, top, pole_w, height), color)
  rl.draw_rectangle_rec(rl.Rectangle(x + pole_w, top, ICON_WIDTH - pole_w, height * 0.45), color)


class NavIndicatorRenderer:
  """At-a-glance state of the two things navigation silently waits on: a GPS fix and a route.

  The pin's colour is always the localizer fix, so the lock state is readable whether or not a
  destination has been set yet. The flag and its label only appear once there is a destination.
  """

  def __init__(self):
    self.nav_status = NavStatus()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)

  def update(self) -> None:
    self.nav_status.update()

  def _label(self) -> str:
    state = self.nav_status.state
    if state == NavState.NO_DESTINATION:
      return tr("GPS locked") if self.nav_status.gps_locked else tr("Waiting for GPS...")
    if state == NavState.WAITING_FOR_GPS:
      return tr("Waiting for GPS...")
    if state == NavState.COMPUTING:
      return tr("Computing route...")
    if state == NavState.NO_ROUTE:
      return self.nav_status.no_route_text
    return self.nav_status.destination

  def render(self, rect: rl.Rectangle) -> None:
    status = self.nav_status
    # hidden unless navigation is opted into and navigationd is actually publishing
    if not status.allow_navigation or status.state == NavState.OFFLINE:
      return

    show_flag = status.state != NavState.NO_DESTINATION
    text = self._label()
    icons_width = ICON_WIDTH + (ICON_GAP + ICON_WIDTH if show_flag else 0)

    max_width = rect.width * MAX_PILL_WIDTH_RATIO
    max_text_width = max_width - icons_width - ICON_GAP - PILL_PADDING * 2
    text_size = measure_text_cached(self._font, text, FONT_SIZE)
    if text_size.x > max_text_width:
      while text_size.x > max_text_width and len(text) > 1:
        text = text[:-1]
        text_size = measure_text_cached(self._font, text + "...", FONT_SIZE)
      text += "..."
      text_size = measure_text_cached(self._font, text, FONT_SIZE)

    width = icons_width + ICON_GAP + text_size.x + PILL_PADDING * 2
    pill = rl.Rectangle(rect.x + (rect.width - width) / 2, rect.y + TOP_OFFSET, width, PILL_HEIGHT)
    rl.draw_rectangle_rounded(pill, 0.35, 10, BACKGROUND)

    failed = status.state == NavState.NO_ROUTE
    text_color = ROUTE_FAILED if failed else TEXT_COLOR

    cy = pill.y + PILL_HEIGHT / 2
    x = pill.x + PILL_PADDING
    _draw_pin(x, cy, GPS_LOCKED if status.gps_locked else GPS_WAITING)
    x += ICON_WIDTH + ICON_GAP
    if show_flag:
      _draw_flag(x, cy, text_color)
      x += ICON_WIDTH + ICON_GAP

    rl.draw_text_ex(self._font, text, rl.Vector2(x, cy - text_size.y / 2), FONT_SIZE, 0, text_color)
