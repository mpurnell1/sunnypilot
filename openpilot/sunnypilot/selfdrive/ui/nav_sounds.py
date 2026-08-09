"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Synthesis for navigation audio cues. Everything is generated at runtime — a 700 Hz CW
sidetone for Morse mode, and four sounds total in Tones mode: a two-note pair whose
contour is the direction (rising = right, falling = left, doubled and faster = the turn
is now), a soft short blip pair for lane changes, a low pair for rerouting, and an
arrival arpeggio. Every maneuver type shares the directional pair — what kind of turn it
is lives on the screen, not in the driver's memory. Every tone edge is a raised-cosine
ramp so nothing clicks on the speaker.
"""
import threading

import numpy as np

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

SAMPLE_RATE = 48000
CW_FREQ = 700.0
BASE_FREQ = 587.33  # D5: the low anchor note has to clear road noise on the device speaker
AMPLITUDE = 0.85
EDGE_S = 0.005  # raised-cosine attack/release

# ticks count roundabout exits or digest miles; a longer run is a malformed code, not a cue
MAX_TICKS = 9

AUDIO_OFF = 0
AUDIO_TONES = 1
AUDIO_MORSE = 2

MORSE = {
  'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.', 'G': '--.',
  'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.',
  'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.', 'S': '...', 'T': '-', 'U': '..-',
  'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--', 'Z': '--..',
  '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-', '5': '.....',
  '6': '-....', '7': '--...', '8': '---..', '9': '----.',
}
# the arrival cue is the AR prosign — di-dah-di-dah-dit run together as one character
PROSIGNS = {'AR': '.-.-.'}


def _tone(freq: float, dur: float, sr: int = SAMPLE_RATE, amp: float = AMPLITUDE) -> np.ndarray:
  n = max(1, int(dur * sr))
  wave = amp * np.sin(2 * np.pi * freq * np.arange(n) / sr).astype(np.float32)
  return wave * _envelope(n, sr)


def _chirp(f0: float, f1: float, dur: float, sr: int = SAMPLE_RATE, amp: float = AMPLITUDE) -> np.ndarray:
  n = max(1, int(dur * sr))
  freqs = np.linspace(f0, f1, n)
  phase = 2 * np.pi * np.cumsum(freqs) / sr
  return (amp * np.sin(phase).astype(np.float32)) * _envelope(n, sr)


def _envelope(n: int, sr: int) -> np.ndarray:
  env = np.ones(n, dtype=np.float32)
  e = min(int(EDGE_S * sr), n // 2)
  if e > 0:
    ramp = (0.5 * (1 - np.cos(np.pi * np.arange(e) / e))).astype(np.float32)
    env[:e] = ramp
    env[-e:] = ramp[::-1]
  return env


def _gap(dur: float, sr: int = SAMPLE_RATE) -> np.ndarray:
  return np.zeros(max(0, int(dur * sr)), dtype=np.float32)


def _note(semitones: float, dur: float, sr: int, amp: float = AMPLITUDE) -> np.ndarray:
  return _tone(BASE_FREQ * 2 ** (semitones / 12), dur, sr, amp)


def morse_wave(code: str, wpm: int, freq: float = CW_FREQ, sr: int = SAMPLE_RATE) -> np.ndarray:
  """Standard timing: dah = 3 dits, intra-character gap 1, inter-character 3, word gap 7."""
  dit = 1.2 / max(5, wpm)
  parts: list[np.ndarray] = []
  chars = [PROSIGNS[code]] if code in PROSIGNS else [MORSE.get(c, ' ') for c in code.upper()]
  for i, char in enumerate(chars):
    if char == ' ':
      parts.append(_gap(4 * dit, sr))  # with the surrounding character gaps this totals 7
      continue
    for j, element in enumerate(char):
      if j > 0:
        parts.append(_gap(dit, sr))
      parts.append(_tone(freq, dit if element == '.' else 3 * dit, sr))
    if i < len(chars) - 1:
      parts.append(_gap(3 * dit, sr))
  return np.concatenate(parts) if parts else _gap(0, sr)


def _ticks(count: int, sr: int) -> list[np.ndarray]:
  parts = []
  for i in range(min(count, MAX_TICKS)):
    if i > 0:
      parts.append(_gap(0.07, sr))
    parts.append(_tone(880.0, 0.05, sr))
  return parts


def _pair(direction: str, note_dur: float, gap_dur: float, sr: int) -> list[np.ndarray]:
  """The directional two-note shape: contour is the entire message."""
  step = {'right': 7, 'left': -7}.get(direction, 0)
  return [_note(0, note_dur, sr), _gap(gap_dur, sr), _note(step, note_dur, sr)]


def earcon_wave(code: str, stage: str, direction: str = 'none', sr: int = SAMPLE_RATE) -> np.ndarray:
  if code == 'QRX':
    return np.concatenate([_note(-2, 0.12, sr, amp=0.55), _gap(0.03, sr), _note(-3, 0.12, sr, amp=0.55)])
  if code == 'AR':
    return np.concatenate([_note(0, 0.09, sr), _gap(0.03, sr), _note(4, 0.09, sr), _gap(0.03, sr), _note(7, 0.09, sr)])
  if code.startswith('C'):
    blip_dir = direction if direction != 'none' else ('left' if code.endswith('L') else 'right')
    step = 4 if blip_dir == 'right' else -4
    return np.concatenate([_note(3, 0.06, sr), _gap(0.03, sr), _note(3 + step, 0.06, sr)])

  fast = stage == 'imminent'
  note_dur = 0.055 if fast else 0.09
  gap_dur = 0.02 if fast else 0.03
  parts = _pair(direction, note_dur, gap_dur, sr)
  if fast:
    parts += [_gap(0.12, sr)] + _pair(direction, note_dur, gap_dur, sr)
  return np.concatenate(parts)


def cue_wave(code: str, stage: str, mode: int, wpm: int, direction: str = 'none', sr: int = SAMPLE_RATE) -> np.ndarray:
  if mode == AUDIO_MORSE:
    return morse_wave(code, wpm, sr=sr)
  return earcon_wave(code, stage, direction, sr=sr)


class NavAudioPlayer:
  """Feeds nav cue samples to soundd's mixer.

  Owns the edge detection on audioCueId and the mode/WPM params; soundd only asks for
  frames and decides whether the channel is free (no alert playing, quiet mode off).

  update() runs on soundd's 20 Hz loop and the rest on the PortAudio callback thread, so
  the buffer and the read position are only ever touched together, under _lock. Synthesis
  stays outside it: the callback must never wait on a cue being built.
  """

  def __init__(self, sr: int = SAMPLE_RATE):
    self.params = Params()
    self.sr = sr
    self.mode: int = 0
    self.wpm: int = 30
    self._frame = 0
    self._last_cue_id: int | None = None
    self._buf = np.zeros(0, dtype=np.float32)
    self._pos = 0
    self._lock = threading.Lock()
    self._bad_codes: set[str] = set()
    self._read_params()

  def _read_params(self) -> None:
    self.mode = self.params.get('NavigationAudio', return_default=True)
    self.wpm = int(np.clip(self.params.get('NavAudioWpm', return_default=True), 5, 60))

  def load_params(self) -> None:
    self._frame += 1
    if self._frame % 50 == 0:  # 2.5 seconds
      self._read_params()

  def update(self, sm) -> None:
    if not sm.updated['navigationd']:
      return
    nav = sm['navigationd']
    cue_id = nav.audioCueId
    if self._last_cue_id is None:
      # cues are sticky on the message, so the one that predates this process must not play
      self._last_cue_id = cue_id
      return
    if cue_id == self._last_cue_id:
      return
    self._last_cue_id = cue_id
    code = str(nav.audioCueCode)
    stage = str(nav.audioCueStage)
    if self.mode == AUDIO_OFF or not code:
      return
    # counting mile-beeps is a Morse skill; in tones the approach prompt covers the turn
    if self.mode == AUDIO_TONES and stage == 'digest':
      return
    try:
      buf = cue_wave(code, stage, self.mode, self.wpm, str(nav.audioCueDirection), self.sr)
    except Exception:
      # a newer navigationd, or a replayed log, can carry codes this build cannot render;
      # letting that out would abort the stream and take soundd down with it
      if code not in self._bad_codes:
        self._bad_codes.add(code)
        cloudlog.exception(f"nav audio: unrenderable cue code {code!r}")
      return
    # a newer cue carries newer information, so it replaces whatever was still playing
    with self._lock:
      self._buf = buf
      self._pos = 0

  @property
  def active(self) -> bool:
    with self._lock:
      return self._pos < len(self._buf)

  def cancel(self) -> None:
    with self._lock:
      self._buf = np.zeros(0, dtype=np.float32)
      self._pos = 0

  def get_frames(self, frames: int) -> np.ndarray:
    out = np.zeros(frames, dtype=np.float32)
    with self._lock:
      take = min(frames, len(self._buf) - self._pos)
      if take > 0:
        out[:take] = self._buf[self._pos:self._pos + take]
        self._pos += take
    return out
