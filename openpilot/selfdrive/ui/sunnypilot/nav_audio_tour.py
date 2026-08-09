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
  AUDIO_MORSE, AUDIO_OFF, AUDIO_TONES, MORSE, PROSIGNS, SAMPLE_RATE, cue_wave, vocabulary_code)
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
  kind: str
  stage: str
  direction: str
  count: int
  title: str
  caption: str
  # the turn card shown while the cue plays: (maneuver type, modifier, distance in
  # meters or None for no distance row); None shows no card at all
  card: tuple[str, str, float | None] | None
  # kinds whose sound only exists in Morse mode; Tones renders them all as the same
  # directional pair, so its tour skips them
  morse_only: bool = False


LANE_CAPTION = tr("The route wants you one lane over. Signal it; on an exit or merge approach the signal alone confirms, before a turn add the usual wheel nudge.")  # noqa: E501

STEPS = [
  Step('turn', 'approach', 'right', 0, tr("Right Turn Ahead"),
       tr("Rising means right. Plays about a quarter mile out, when this card appears."), ('turn', 'right', APPROACH_M)),
  Step('turn', 'imminent', 'right', 0, tr("Right Turn Now"),
       tr("The same sound, faster and doubled: about 300 feet to go."), ('turn', 'right', IMMINENT_M)),
  Step('turn', 'approach', 'left', 0, tr("Left Turn"),
       tr("Falling means left. One sound covers every kind of turn; the card shows which."), ('turn', 'left', APPROACH_M)),
  Step('slightTurn', 'approach', 'left', 0, tr("Slight Left"),
       tr("A shallow bend rather than a full turn."), ('turn', 'slightLeft', APPROACH_M), morse_only=True),
  Step('slightTurn', 'approach', 'right', 0, tr("Slight Right"),
       tr("A shallow bend rather than a full turn."), ('turn', 'slightRight', APPROACH_M), morse_only=True),
  Step('sharpTurn', 'approach', 'left', 0, tr("Sharp Left"),
       tr("Sharper than a right angle."), ('turn', 'sharpLeft', APPROACH_M), morse_only=True),
  Step('sharpTurn', 'approach', 'right', 0, tr("Sharp Right"),
       tr("Sharper than a right angle."), ('turn', 'sharpRight', APPROACH_M), morse_only=True),
  Step('keep', 'approach', 'left', 0, tr("Keep Left"),
       tr("The road forks; stay to the left."), ('fork', 'slightLeft', APPROACH_M), morse_only=True),
  Step('keep', 'approach', 'right', 0, tr("Keep Right"),
       tr("The road forks; stay to the right."), ('fork', 'slightRight', APPROACH_M), morse_only=True),
  Step('exit', 'approach', 'left', 0, tr("Exit Left"),
       tr("Leave the highway on the left."), ('off ramp', 'slightLeft', APPROACH_M), morse_only=True),
  Step('exit', 'approach', 'right', 0, tr("Exit Right"),
       tr("Leave the highway on the right."), ('off ramp', 'slightRight', APPROACH_M), morse_only=True),
  Step('merge', 'approach', 'left', 0, tr("Merge Left"),
       tr("The lane joins traffic on the left."), ('merge', 'slightLeft', APPROACH_M), morse_only=True),
  Step('merge', 'approach', 'right', 0, tr("Merge Right"),
       tr("The lane joins traffic on the right."), ('merge', 'slightRight', APPROACH_M), morse_only=True),
  Step('uturn', 'approach', 'left', 0, tr("U-Turn"),
       tr("Turn back the way you came."), ('turn', 'uturn', APPROACH_M), morse_only=True),
  Step('roundabout', 'approach', 'right', 3, tr("Roundabout, 3rd Exit"),
       tr("The exit number follows the O: copy the digit."), ('roundabout', 'right', APPROACH_M), morse_only=True),
  Step('laneChange', 'lane', 'left', 0, tr("Lane Change Left"), LANE_CAPTION, ('lane', 'slightLeft', None)),
  Step('laneChange', 'lane', 'right', 0, tr("Lane Change Right"), LANE_CAPTION, ('lane', 'slightRight', None)),
  Step('turn', 'digest', 'right', 3, tr("Next Turn in 3 Miles"),
       tr("After a maneuver with a long quiet stretch ahead: the turn code, then the mile count."),
       ('turn', 'right', 3 * METERS_PER_MILE), morse_only=True),
  Step('reroute', 'reroute', 'none', 0, tr("Rerouting"),
       tr("You've left the route. QRX, ham radio for 'stand by', plays once while a new route is computed."), None),
  Step('arrive', 'arrive', 'none', 0, tr("Arrived"),
       tr("The AR prosign signs the route off. You're there."), ('arrive', 'none', 30.0)),
]


def display_code(step: Step) -> str:
  code = vocabulary_code(step.kind, step.direction, 0 if step.stage == 'digest' else step.count)
  if step.stage == 'digest' and step.count:
    code = f'{code} {step.count}'
  return code


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

    self._steps: list[Step] = STEPS
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

    # tones collapses the maneuver vocabulary into one directional pair, so its tour
    # only teaches the sounds that actually exist in that mode
    self._steps = [step for step in STEPS if mode == AUDIO_MORSE or not step.morse_only]

    self._tmpdir = tempfile.mkdtemp(prefix='nav_tour_')
    for i, step in enumerate(self._steps):
      samples = cue_wave(step.kind, step.stage, step.direction, step.count, mode, wpm)
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
    if self._step >= len(self._steps):
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
    step = self._steps[min(self._step, len(self._steps) - 1)] if self._step >= 0 else self._steps[0]

    # card on the left third, words on the right
    card_cx = rect.x + rect.width * 0.22
    if step.card is not None:
      self._draw_card(card_cx, rect.y + rect.height * 0.18, step.card)

    code = display_code(step)
    code_line = f'{code}    {morse_text(code)}'
    size = measure_text_cached(self._font, code_line, 64)
    rl.draw_text_ex(self._font, code_line, rl.Vector2(card_cx - size.x / 2, rect.y + rect.height * 0.72), 64, 0, TURN_COLOR)

    text_x = rect.x + rect.width * 0.42
    text_w = rect.width * 0.50
    self._draw_wrapped(self._title_font, step.title, text_x, rect.y + rect.height * 0.20, text_w, 84)
    self._draw_wrapped(self._caption_font, step.caption, text_x, rect.y + rect.height * 0.42, text_w, 52)

    hint = tr("Tap to skip ahead")
    hint_size = measure_text_cached(self._font, hint, 40)
    rl.draw_text_ex(self._font, hint, rl.Vector2(rect.x + rect.width - hint_size.x - 60, rect.y + rect.height - 90), 40, 0, rl.Color(255, 255, 255, 120))

    w = int(((self._step + 1) / len(self._steps)) * rect.width)
    rl.draw_rectangle(int(rect.x), int(rect.y + rect.height - 20), w, 20, PROGRESS_COLOR)
    return -1
