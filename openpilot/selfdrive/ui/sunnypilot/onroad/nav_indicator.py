"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG
from openpilot.selfdrive.ui.sunnypilot.nav_status import NavState, NavStatus
from openpilot.selfdrive.ui.ui_state import ui_state

# The set speed box starts at x+60, y+45 and is set_speed_height tall; the speed limit signs
# stack in the column to its right. This drops into the empty space directly below it.
LEFT_MARGIN = 60
TOP_OFFSET = 45 + UI_CONFIG.set_speed_height + 20

BOX_HEIGHT = 84
ICON_WIDTH = 44
ICON_GAP = 26

BACKGROUND = rl.Color(0, 0, 0, 140)
GOOD = rl.Color(0x2f, 0xc4, 0x6e, 0xff)
BAD = rl.Color(0xf2, 0x4b, 0x4b, 0xff)


def _draw_pin(cx: float, cy: float, color: rl.Color) -> None:
  radius = ICON_WIDTH / 2
  head_y = cy - radius * 0.35

  # draw_poly handles vertex winding, unlike draw_triangle
  rl.draw_poly(rl.Vector2(cx, head_y + radius * 0.75), 3, radius * 0.95, 90, color)
  rl.draw_circle(int(cx), int(head_y), radius, color)
  rl.draw_circle(int(cx), int(head_y), radius * 0.38, BACKGROUND)


def _draw_flag(cx: float, cy: float, color: rl.Color) -> None:
  pole_w = max(3.0, ICON_WIDTH * 0.12)
  height = ICON_WIDTH * 1.05
  x = cx - ICON_WIDTH / 2
  top = cy - height / 2

  rl.draw_rectangle_rec(rl.Rectangle(x, top, pole_w, height), color)
  rl.draw_rectangle_rec(rl.Rectangle(x + pole_w, top, ICON_WIDTH - pole_w, height * 0.45), color)


# pin is the GPS fix, flag is the route. The flag needs a destination to mean anything, but the
# pin does not, so it stays visible on its own.
class NavIndicatorRenderer:
  def __init__(self):
    self.nav_status = NavStatus()

  def update(self) -> None:
    self.nav_status.update()

  def render(self, rect: rl.Rectangle) -> None:
    status = self.nav_status
    # hidden unless navigation is opted into and navigationd is actually publishing
    if not status.allow_navigation or status.state == NavState.OFFLINE:
      return

    # each icon is individually switchable from Settings > Navigation
    show_pin = status.show_gps_icon
    show_flag = status.show_route_icon and status.state != NavState.NO_DESTINATION
    if not (show_pin or show_flag):
      return

    width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    box = rl.Rectangle(rect.x + LEFT_MARGIN, rect.y + TOP_OFFSET, width, BOX_HEIGHT)
    rl.draw_rectangle_rounded(box, 0.35, 10, BACKGROUND)

    cy = box.y + BOX_HEIGHT / 2
    span = ICON_WIDTH * (show_pin + show_flag) + ICON_GAP * (show_pin and show_flag)
    x = box.x + (box.width - span) / 2 + ICON_WIDTH / 2

    if show_pin:
      _draw_pin(x, cy, GOOD if status.gps_locked else BAD)
      x += ICON_WIDTH + ICON_GAP
    if show_flag:
      _draw_flag(x, cy, GOOD if status.state == NavState.ACTIVE else BAD)
