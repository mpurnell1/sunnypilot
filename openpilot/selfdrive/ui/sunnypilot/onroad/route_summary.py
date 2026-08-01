"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from datetime import datetime, timedelta

import pyray as rl

from openpilot.selfdrive.ui.sunnypilot.nav_status import NavState, NavStatus
from openpilot.selfdrive.ui.sunnypilot.onroad.developer_ui import get_bottom_dev_ui_offset
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import format_distance
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

RIGHT_MARGIN = 60
BOTTOM_MARGIN = 45
HEIGHT = 88
PADDING = 44
SEGMENT_GAP = 14
FONT_SIZE = 40

BACKGROUND = rl.Color(0, 0, 0, 140)
PRIMARY = rl.Color(255, 255, 255, 255)
SECONDARY = rl.Color(255, 255, 255, 217)
SEPARATOR = rl.Color(255, 255, 255, 115)
ETA_COLOR = rl.Color(0x2f, 0xc4, 0x6e, 0xff)


def format_remaining_time(seconds: float) -> str:
  minutes = max(0, round(seconds / 60))
  if minutes < 60:
    return f"{minutes} min"
  return f"{minutes // 60} hr {minutes % 60} min"


# remaining time · remaining distance · arrival time, anchored to the bottom-right corner.
# ETA lives here rather than in the message so the clock stays current between route updates.
class RouteSummaryRenderer:
  def __init__(self):
    self.nav_status = NavStatus()
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_semi = gui_app.font(FontWeight.SEMI_BOLD)

  def update(self) -> None:
    self.nav_status.update()

  def render(self, rect: rl.Rectangle) -> None:
    status = self.nav_status
    if not status.allow_navigation or status.state != NavState.ACTIVE:
      return

    msg = ui_state.sm['navigationd']
    eta = (datetime.now() + timedelta(seconds=float(msg.timeRemaining))).strftime('%-I:%M %p').lower()
    segments = [
      (format_remaining_time(msg.timeRemaining), self._font_bold, PRIMARY),
      ('·', self._font_semi, SEPARATOR),
      (format_distance(msg.distanceRemaining, ui_state.is_metric), self._font_semi, SECONDARY),
      ('·', self._font_semi, SEPARATOR),
      (eta, self._font_semi, ETA_COLOR),
    ]

    sizes = [measure_text_cached(font, text, FONT_SIZE) for text, font, _ in segments]
    width = sum(size.x for size in sizes) + SEGMENT_GAP * (len(segments) - 1) + 2 * PADDING

    # the bottom developer UI bar claims the last 60px of the screen, so climb above it
    box = rl.Rectangle(rect.x + rect.width - RIGHT_MARGIN - width,
                       rect.y + rect.height - BOTTOM_MARGIN - HEIGHT - get_bottom_dev_ui_offset(),
                       width, HEIGHT)
    rl.draw_rectangle_rounded(box, 0.35, 10, BACKGROUND)

    x = box.x + PADDING
    for (text, font, color), size in zip(segments, sizes, strict=True):
      rl.draw_text_ex(font, text, rl.Vector2(x, box.y + (HEIGHT - size.y) / 2), FONT_SIZE, 0, color)
      x += size.x + SEGMENT_GAP
