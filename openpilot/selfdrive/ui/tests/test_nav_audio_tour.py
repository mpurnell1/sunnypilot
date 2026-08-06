"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.selfdrive.ui.sunnypilot.nav_audio_tour import STEPS, morse_text
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import ARROW_ANGLES
from openpilot.sunnypilot.selfdrive.ui.nav_sounds import AUDIO_MORSE, AUDIO_TONES, cue_wave


class TestTourSteps:
  def test_every_step_renders_in_both_modes(self):
    for step in STEPS:
      for mode in (AUDIO_TONES, AUDIO_MORSE):
        wave = cue_wave(step.code, step.stage, mode, 30)
        assert len(wave) > 0 and np.all(np.isfinite(wave)), step.code

  def test_tour_covers_the_vocabulary(self):
    codes = {step.code.split(' ')[0] for step in STEPS}
    assert codes >= {'L', 'R', 'SL', 'SR', 'HL', 'HR', 'U', 'ML', 'MR',
                     'XL', 'XR', 'KL', 'KR', 'O3', 'CL', 'CR', 'QRX', 'AR'}

  def test_cards_use_real_modifiers(self):
    for step in STEPS:
      if step.card is not None:
        _, modifier, _ = step.card
        assert modifier in ARROW_ANGLES or modifier == 'uturn', step.code

  def test_every_code_has_a_morse_line(self):
    for step in STEPS:
      line = morse_text(step.code)
      assert line and set(line) <= set('.- /'), step.code
