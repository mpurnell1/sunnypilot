"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from types import SimpleNamespace

import numpy as np

from openpilot.sunnypilot.navd.nav_audio import NavAudioCues, maneuver_code
from openpilot.sunnypilot.selfdrive.ui import nav_sounds
from openpilot.sunnypilot.selfdrive.ui.nav_sounds import AUDIO_MORSE, NavAudioPlayer, earcon_wave, morse_wave

# at exactly the 22.4 m/s breakpoint the stage distances are 500 m (approach) and 130 m (imminent)
V_CRUISE = 22.4


def _progress(distance: float, mtype: str = 'turn', modifier: str = 'right', step_idx: int = 3,
              step_len: float = 2000.0, instruction: str = '') -> dict:
  return {
    'current_step_idx': step_idx,
    'distance_to_end_of_step': distance,
    'current_step': {'distance': step_len},
    'next_turn': {'maneuver': mtype, 'modifier': modifier, 'instruction': instruction},
  }


class TestManeuverCodes:
  def test_turns(self):
    assert maneuver_code('turn', 'left') == 'L'
    assert maneuver_code('turn', 'right') == 'R'
    assert maneuver_code('turn', 'slightLeft') == 'SL'
    assert maneuver_code('turn', 'sharpRight') == 'HR'
    assert maneuver_code('turn', 'uturn') == 'U'
    assert maneuver_code('end of road', 'left') == 'L'

  def test_typed_maneuvers_carry_their_prefix(self):
    assert maneuver_code('off ramp', 'slightRight') == 'XR'
    assert maneuver_code('merge', 'slightLeft') == 'ML'
    assert maneuver_code('fork', 'right') == 'KR'

  def test_non_actions_are_silent(self):
    assert maneuver_code('continue', 'straight') == ''
    assert maneuver_code('merge', 'straight') == ''
    assert maneuver_code('depart', 'right') == ''
    assert maneuver_code('arrive', 'none') == ''

  def test_bends_and_name_changes_are_not_turns(self):
    assert maneuver_code('continue', 'left') == ''
    assert maneuver_code('continue', 'slightRight') == ''
    assert maneuver_code('new name', 'slightLeft') == ''
    assert maneuver_code('new name', 'right') == ''
    # a turn onto a road that also changes name still arrives as a turn
    assert maneuver_code('turn', 'left') == 'L'

  def test_a_continue_uturn_is_still_a_uturn(self):
    assert maneuver_code('continue', 'uturn') == 'U'

  def test_roundabout_exit_number_comes_from_instruction(self):
    assert maneuver_code('roundabout', 'right', 'Enter the roundabout and take the 3rd exit') == 'O3'
    assert maneuver_code('rotary', 'right') == 'O'
    assert maneuver_code('exit roundabout', 'right') == ''

  def test_only_an_ordinal_reads_as_an_exit_number(self):
    assert maneuver_code('roundabout', 'right', 'At the roundabout, exit onto A40 exit') == 'O'
    assert maneuver_code('roundabout', 'right', 'Take the 1st exit onto A40') == 'O1'
    assert maneuver_code('roundabout', 'right', 'Take the 12th exit') == 'O9'


class TestStages:
  def test_two_stage_prompt_never_repeats(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(600.0), {}, V_CRUISE, False)
    assert cues.cue_id == 0
    cues.update(route, _progress(450.0), {}, V_CRUISE, False)
    assert (cues.code, cues.stage, cues.cue_id) == ('R', 'approach', 1)
    cues.update(route, _progress(440.0), {}, V_CRUISE, False)
    assert cues.cue_id == 1
    cues.update(route, _progress(100.0), {}, V_CRUISE, False)
    assert (cues.code, cues.stage, cues.cue_id) == ('R', 'imminent', 2)
    cues.update(route, _progress(60.0), {}, V_CRUISE, False)
    assert cues.cue_id == 2

  def test_chained_maneuvers_skip_the_approach(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(450.0, step_len=300.0), {}, V_CRUISE, False)
    assert cues.cue_id == 0
    cues.update(route, _progress(100.0, step_len=300.0), {}, V_CRUISE, False)
    assert (cues.stage, cues.cue_id) == ('imminent', 1)

  def test_crawling_defers_the_approach(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(100.0), {}, 2.0, False)
    assert cues.cue_id == 0
    cues.update(route, _progress(100.0), {}, 10.0, False)
    assert (cues.stage, cues.cue_id) == ('approach', 1)

  def test_route_change_resets_fired_state(self):
    cues = NavAudioCues()
    cues.update({}, _progress(450.0), {}, V_CRUISE, False)
    assert cues.cue_id == 1
    cues.update({}, _progress(450.0), {}, V_CRUISE, False)
    assert (cues.stage, cues.cue_id) == ('approach', 2)

  def test_no_progress_no_cue(self):
    cues = NavAudioCues()
    cues.update(None, None, {}, V_CRUISE, False)
    assert cues.cue_id == 0


class TestEventCues:
  def test_lane_cue_yields_to_the_stage_prompt(self):
    cues, route = NavAudioCues(), {}
    nav_data = {'lane_change_direction': 'left'}
    cues.update(route, _progress(450.0), nav_data, V_CRUISE, False)
    assert (cues.code, cues.stage) == ('R', 'approach')
    cues.update(route, _progress(440.0), nav_data, V_CRUISE, False)
    assert (cues.code, cues.stage, cues.cue_id) == ('CL', 'lane', 2)
    cues.update(route, _progress(430.0), nav_data, V_CRUISE, False)
    assert cues.cue_id == 2

  def test_reroute_fires_once_per_excursion(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(5000.0, modifier='straight', mtype='continue'), {}, V_CRUISE, True)
    assert (cues.code, cues.stage, cues.cue_id) == ('QRX', 'reroute', 1)
    cues.update(route, _progress(5000.0, modifier='straight', mtype='continue'), {}, V_CRUISE, True)
    assert cues.cue_id == 1
    cues.update(route, _progress(5000.0, modifier='straight', mtype='continue'), {}, V_CRUISE, False)
    cues.update(route, _progress(5000.0, modifier='straight', mtype='continue'), {}, V_CRUISE, True)
    assert (cues.stage, cues.cue_id) == ('reroute', 2)

  def test_arrival_fires_once(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(20.0, mtype='arrive', modifier='none'), {'arrived': True}, 0.5, False)
    assert (cues.code, cues.stage, cues.cue_id) == ('AR', 'arrive', 1)
    cues.update(route, _progress(20.0, mtype='arrive', modifier='none'), {'arrived': True}, 0.5, False)
    assert cues.cue_id == 1

  def test_long_gap_earns_a_mileage_digest(self):
    cues, route = NavAudioCues(), {}
    cues.update(route, _progress(5000.0), {}, V_CRUISE, False)
    assert (cues.code, cues.stage, cues.cue_id) == ('R 3', 'digest', 1)
    cues.update(route, _progress(4900.0), {}, V_CRUISE, False)
    assert cues.cue_id == 1


ALL_CODES = ['L', 'R', 'SL', 'SR', 'HL', 'HR', 'U', 'ML', 'MR', 'XL', 'XR', 'KL', 'KR',
             'O', 'O3', 'CL', 'CR', 'QRX', 'AR']


class TestSynthesis:
  def test_morse_timing_is_standard(self):
    dit = int(1.2 / 30 * nav_sounds.SAMPLE_RATE)
    assert len(morse_wave('E', 30)) == dit
    # A = dit gap dah = 5 dits; R adds a 3-dit gap then dit gap dah gap dit = 7 dits
    assert len(morse_wave('AR', 30)) < len(morse_wave('A', 30)) + len(morse_wave('R', 30)) + 3 * dit
    # the prosign runs the elements together as one character: .-.-. = 13 dits
    assert len(morse_wave('AR', 30)) == 13 * dit

  def test_morse_word_gap(self):
    assert len(morse_wave('R 5', 30)) > len(morse_wave('R5', 30))

  def test_every_code_renders_in_both_modes(self):
    for code in ALL_CODES:
      for stage in ('approach', 'imminent'):
        wave = earcon_wave(code, stage)
        assert wave.dtype == np.float32 and len(wave) > 0
        assert np.all(np.isfinite(wave)) and np.max(np.abs(wave)) <= nav_sounds.AMPLITUDE + 1e-6
      assert len(morse_wave(code, 30)) > 0

  def test_digest_appends_ticks(self):
    assert len(earcon_wave('R 5', 'digest')) > len(earcon_wave('R', 'digest'))

  def test_tick_runs_are_clamped(self):
    assert len(earcon_wave('O40', 'approach')) == len(earcon_wave('O9', 'approach'))
    assert len(earcon_wave('R 40', 'digest')) == len(earcon_wave('R 9', 'digest'))

  def test_envelopes_kill_clicks(self):
    wave = earcon_wave('R', 'approach')
    assert abs(wave[0]) < 1e-3 and abs(wave[-1]) < 1e-3


class _FakeSM:
  def __init__(self, cue_id: int, code: str = 'R', stage: str = 'approach'):
    self.updated = {'navigationd': True}
    self._msg = SimpleNamespace(audioCueId=cue_id, audioCueCode=code, audioCueStage=stage)

  def __getitem__(self, _):
    return self._msg


class TestPlayer:
  def _player(self, monkeypatch, mode: int) -> NavAudioPlayer:
    monkeypatch.setattr(NavAudioPlayer, '_read_params', lambda self: None)
    player = NavAudioPlayer()
    player.mode = mode
    return player

  def test_late_subscriber_swallows_the_sticky_cue(self, monkeypatch):
    player = self._player(monkeypatch, AUDIO_MORSE)
    player.update(_FakeSM(7))
    assert not player.active
    player.update(_FakeSM(8))
    assert player.active

  def test_off_mode_stays_silent(self, monkeypatch):
    player = self._player(monkeypatch, nav_sounds.AUDIO_OFF)
    player.update(_FakeSM(1))
    player.update(_FakeSM(2))
    assert not player.active

  def test_an_unrenderable_code_stays_silent(self, monkeypatch):
    logged = []
    monkeypatch.setattr(nav_sounds.cloudlog, 'exception', lambda msg: logged.append(msg))
    player = self._player(monkeypatch, nav_sounds.AUDIO_TONES)
    player.update(_FakeSM(1, code='ZZ'))
    player.update(_FakeSM(2, code='ZZ'))
    player.update(_FakeSM(3, code='ZZ'))
    assert not player.active
    assert len(logged) == 1
    # the next cue this build does understand still plays
    player.update(_FakeSM(4, code='R'))
    assert player.active

  def test_alert_cancel_drops_the_transmission(self, monkeypatch):
    player = self._player(monkeypatch, AUDIO_MORSE)
    player.update(_FakeSM(1))
    player.update(_FakeSM(2))
    frames = player.get_frames(256)
    assert np.any(frames != 0)
    player.cancel()
    assert not player.active
    assert np.all(player.get_frames(256) == 0)
