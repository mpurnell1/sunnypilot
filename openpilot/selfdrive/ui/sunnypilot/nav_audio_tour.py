"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The navigation audio tour: plays every cue while showing the same turn card the drive
would, so each sound is learned against the exact screen it will accompany. Audio runs on
raylib's own device in the UI process — soundd is not up offroad, where the tour is used,
and owning playback is also what keeps the card and the sound exactly together.
"""
import os
import tempfile
import time
import wave as wavelib
from typing import NamedTuple

import numpy as np
import pyray as rl

from openpilot.common.params import Params
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import (
  ARROW_ANGLES, BACKGROUND, METERS_PER_MILE, TURN_BOX_HEIGHT, TURN_COLOR, TURN_FONT_SIZE,
  TURN_ICON_CY, TURN_ICON_WIDTH, TURN_TEXT_TOP, _draw_flag, _draw_fork, _draw_merge,
  _draw_roundabout, _draw_turn, _draw_uturn, format_distance)
from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.navd.helpers import ROUNDABOUT_TYPES
from openpilot.sunnypilot.selfdrive.ui.nav_sounds import (
  AUDIO_OFF, AUDIO_TONES, MORSE, PROSIGNS, SAMPLE_RATE, cue_wave)
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.wrap_text import wrap_text
from openpilot.system.ui.widgets import Widget

# the tour card is the onroad card scaled up to presentation size
CARD_SCALE = 2.5
HOLD_S = 1.2  # quiet time after each cue before the next
PROGRESS_COLOR = rl.Color(70, 91, 234, 255)

APPROACH_M = 500.0
IMMINENT_M = 90.0


class Step(NamedTuple):
  code: str
  stage: str
  title: str
  caption: str
  # the turn card shown while the cue plays: (maneuver type, modifier, distance in
  # meters or None for no distance row); None shows no card at all
  card: tuple[str, str, float | None] | None


STEPS = [
  Step('R', 'approach', tr("Right Turn Ahead"),
       tr("Plays about a quarter mile from the turn, when this card appears."), ('turn', 'right', APPROACH_M)),
  Step('R', 'imminent', tr("Right Turn Now"),
       tr("The same sound, faster and doubled: the turn is about 300 feet away."), ('turn', 'right', IMMINENT_M)),
  Step('L', 'approach', tr("Left Turn"),
       tr("Left is always the falling version of the same sound."), ('turn', 'left', APPROACH_M)),
  Step('SL', 'approach', tr("Slight Left"),
       tr("A narrow interval for a shallow bend."), ('turn', 'slightLeft', APPROACH_M)),
  Step('SR', 'approach', tr("Slight Right"),
       tr("The rising mirror of slight left."), ('turn', 'slightRight', APPROACH_M)),
  Step('HL', 'approach', tr("Sharp Left"),
       tr("The widest interval: the turn is sharper than 90 degrees."), ('turn', 'sharpLeft', APPROACH_M)),
  Step('HR', 'approach', tr("Sharp Right"),
       tr("The rising mirror of sharp left."), ('turn', 'sharpRight', APPROACH_M)),
  Step('KL', 'approach', tr("Keep Left"),
       tr("A repeated note first: the road forks and your side is left."), ('fork', 'slightLeft', APPROACH_M)),
  Step('KR', 'approach', tr("Keep Right"),
       tr("A repeated note first: the road forks and your side is right."), ('fork', 'slightRight', APPROACH_M)),
  Step('XL', 'approach', tr("Exit Left"),
       tr("A high tick first: leave the highway on the left."), ('off ramp', 'slightLeft', APPROACH_M)),
  Step('XR', 'approach', tr("Exit Right"),
       tr("A high tick first: leave the highway on the right."), ('off ramp', 'slightRight', APPROACH_M)),
  Step('ML', 'approach', tr("Merge Left"),
       tr("A slide instead of steps: traffic joins from your lane's side."), ('merge', 'slightLeft', APPROACH_M)),
  Step('MR', 'approach', tr("Merge Right"),
       tr("The rising slide, merging to the right."), ('merge', 'slightRight', APPROACH_M)),
  Step('U', 'approach', tr("U-Turn"),
       tr("Down and back up, the shape of the maneuver."), ('turn', 'uturn', APPROACH_M)),
  Step('O3', 'approach', tr("Roundabout, 3rd Exit"),
       tr("The circling figure, then one tick per exit. Count the ticks."), ('roundabout', 'right', APPROACH_M)),
  Step('CL', 'lane', tr("Lane Change Left"),
       tr("The route wants you one lane over. Signal left and the car confirms when it's safe."), ('lane', 'slightLeft', None)),
  Step('CR', 'lane', tr("Lane Change Right"),
       tr("The route wants you one lane over. Signal right and the car confirms when it's safe."), ('lane', 'slightRight', None)),
  Step('R 3', 'digest', tr("Next Turn in 3 Miles"),
       tr("After a maneuver with a long quiet stretch ahead: the turn sound, then one tick per mile."),
       ('turn', 'right', 3 * METERS_PER_MILE)),
  Step('QRX', 'reroute', tr("Rerouting"),
       tr("You've left the route. QRX, ham radio for 'stand by', plays once while a new route is computed."), None),
  Step('AR', 'arrive', tr("Arrived"),
       tr("The AR prosign signs the route off. You're there."), ('arrive', 'none', 30.0)),
]


def morse_text(code: str) -> str:
  # plain ASCII dits and dahs: the UI font atlas only loads the default codepoints
  if code in PROSIGNS:
    return PROSIGNS[code]
  return '   '.join(MORSE.get(c, '/') for c in code.upper() if c in MORSE or c == ' ')


class NavAudioTour(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    self._title_font = gui_app.font(FontWeight.BOLD)
    self._caption_font = gui_app.font(FontWeight.MEDIUM)

    self._sounds: list = []
    self._durations: list[float] = []
    self._tmpdir: str | None = None
    self._step = -1
    self._step_started = 0.0

  def show_event(self):
    # synthesized fresh per run: the mode or the Morse speed may have changed in settings
    mode = self._params.get('NavigationAudio', return_default=True)
    if mode == AUDIO_OFF:
      mode = AUDIO_TONES
    wpm = int(np.clip(self._params.get('NavAudioWpm', return_default=True), 5, 60))

    if not rl.is_audio_device_ready():
      rl.init_audio_device()

    self._tmpdir = tempfile.mkdtemp(prefix='nav_tour_')
    for i, step in enumerate(STEPS):
      samples = cue_wave(step.code, step.stage, mode, wpm)
      path = os.path.join(self._tmpdir, f'step{i}.wav')
      with wavelib.open(path, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
      self._sounds.append(rl.load_sound(path))
      self._durations.append(len(samples) / SAMPLE_RATE)

    self._step = -1
    self._advance()

  def hide_event(self):
    if 0 <= self._step < len(self._sounds):
      rl.stop_sound(self._sounds[self._step])
    for sound in self._sounds:
      rl.unload_sound(sound)
    self._sounds = []
    self._durations = []
    if self._tmpdir is not None:
      for name in os.listdir(self._tmpdir):
        os.remove(os.path.join(self._tmpdir, name))
      os.rmdir(self._tmpdir)
      self._tmpdir = None

  def _advance(self):
    if 0 <= self._step < len(self._sounds):
      rl.stop_sound(self._sounds[self._step])
    self._step += 1
    if self._step >= len(STEPS):
      gui_app.pop_widget()
      return
    self._step_started = time.monotonic()
    rl.play_sound(self._sounds[self._step])

  def _handle_mouse_release(self, mouse_pos):
    self._advance()

  def _update_state(self):
    if self._step >= 0 and time.monotonic() - self._step_started > self._durations[self._step] + HOLD_S:
      self._advance()

  def _draw_card(self, cx: float, top: float, card: tuple[str, str, float | None]) -> None:
    maneuver_type, modifier, distance = card
    s = CARD_SCALE
    width = (UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial) * s
    box = rl.Rectangle(cx - width / 2, top, width, TURN_BOX_HEIGHT * s)
    rl.draw_rectangle_rounded(box, 0.175, 10, BACKGROUND)

    icon_cx = box.x + box.width / 2
    icon_cy = box.y + TURN_ICON_CY * s
    angle = ARROW_ANGLES.get(modifier, 0)
    # the same glyph dispatch the onroad card uses, so the tour shows the real thing
    if maneuver_type == 'arrive':
      _draw_flag(icon_cx, icon_cy, TURN_COLOR, TURN_ICON_WIDTH * s)
    elif any(t in maneuver_type for t in ROUNDABOUT_TYPES):
      _draw_roundabout(icon_cx, icon_cy, TURN_COLOR, TURN_ICON_WIDTH * s)
    elif modifier == 'uturn':
      _draw_uturn(icon_cx, icon_cy, TURN_COLOR, TURN_ICON_WIDTH * s)
    elif maneuver_type in ('off ramp', 'fork') and angle != 0:
      _draw_fork(icon_cx, icon_cy, 1.0 if angle > 0 else -1.0, TURN_COLOR, TURN_ICON_WIDTH * s, exit_ramp=maneuver_type == 'off ramp')
    elif maneuver_type == 'merge' and angle != 0:
      _draw_merge(icon_cx, icon_cy, 1.0 if angle > 0 else -1.0, TURN_COLOR, TURN_ICON_WIDTH * s)
    else:
      _draw_turn(icon_cx, icon_cy, angle, TURN_COLOR, TURN_ICON_WIDTH * s)

    if distance is not None:
      text = format_distance(distance, ui_state.is_metric)
      size = measure_text_cached(self._font, text, int(TURN_FONT_SIZE * s))
      rl.draw_text_ex(self._font, text, rl.Vector2(icon_cx - size.x / 2, box.y + TURN_TEXT_TOP * s), int(TURN_FONT_SIZE * s), 0, TURN_COLOR)

  def _draw_wrapped(self, font, text: str, x: float, y: float, width: float, font_size: int, line_gap: float = 1.25) -> None:
    for line in wrap_text(font, text, font_size, int(width)):
      rl.draw_text_ex(font, line, rl.Vector2(x, y), font_size, 0, TURN_COLOR)
      y += font_size * line_gap

  def _render(self, rect):
    rl.draw_rectangle_rec(rect, rl.Color(18, 18, 18, 255))
    step = STEPS[min(self._step, len(STEPS) - 1)] if self._step >= 0 else STEPS[0]

    # card on the left third, words on the right
    card_cx = rect.x + rect.width * 0.22
    if step.card is not None:
      self._draw_card(card_cx, rect.y + rect.height * 0.18, step.card)

    code_line = f'{step.code}    {morse_text(step.code)}'
    size = measure_text_cached(self._font, code_line, 64)
    rl.draw_text_ex(self._font, code_line, rl.Vector2(card_cx - size.x / 2, rect.y + rect.height * 0.72), 64, 0, TURN_COLOR)

    text_x = rect.x + rect.width * 0.42
    text_w = rect.width * 0.50
    self._draw_wrapped(self._title_font, step.title, text_x, rect.y + rect.height * 0.20, text_w, 84)
    self._draw_wrapped(self._caption_font, step.caption, text_x, rect.y + rect.height * 0.42, text_w, 52)

    hint = tr("Tap to skip ahead")
    hint_size = measure_text_cached(self._font, hint, 40)
    rl.draw_text_ex(self._font, hint, rl.Vector2(rect.x + rect.width - hint_size.x - 60, rect.y + rect.height - 90), 40, 0, rl.Color(255, 255, 255, 120))

    w = int(((self._step + 1) / len(STEPS)) * rect.width)
    rl.draw_rectangle(int(rect.x), int(rect.y + rect.height - 20), w, 20, PROGRESS_COLOR)
    return -1
