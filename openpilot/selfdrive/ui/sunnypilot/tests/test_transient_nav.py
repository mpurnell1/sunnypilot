"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from types import SimpleNamespace

from openpilot.cereal import custom

from openpilot.selfdrive.ui.sunnypilot.nav_status import NavState
from openpilot.selfdrive.ui.sunnypilot.onroad.transient_nav import (
  ChipMode, TransientNav, TransientNavState, chip_mode, maneuver_signature, pick_upcoming_maneuver,
)


def _maneuvers(*specs):
  msg = custom.Navigationd.new_message()
  msg.init('allManeuvers', len(specs))
  for m, (maneuver_type, modifier, distance) in zip(msg.allManeuvers, specs, strict=True):
    m.type = maneuver_type
    m.modifier = modifier
    m.distance = distance
  return msg.allManeuvers


class TestPickUpcomingManeuver:
  def test_prefers_second_entry(self):
    picked = pick_upcoming_maneuver(_maneuvers(('depart', 'none', 120.0), ('turn', 'right', 480.0)))
    assert picked is not None
    maneuver_type, modifier, distance = picked
    assert (maneuver_type, modifier) == ('turn', 'right')
    assert abs(distance - 480.0) < 1e-6

  def test_lone_arrive_still_shows(self):
    picked = pick_upcoming_maneuver(_maneuvers(('arrive', 'none', 60.0)))
    assert picked is not None
    assert picked[0] == 'arrive'

  def test_lone_non_arrive_hidden(self):
    assert pick_upcoming_maneuver(_maneuvers(('depart', 'none', 120.0))) is None

  def test_empty_hidden(self):
    assert pick_upcoming_maneuver(_maneuvers()) is None


# a short route: the depart step behind the car, a right turn ahead, then the arrival
FULL_ROUTE = (('depart', 'straight', 0.0), ('turn', 'right', 400.0), ('arrive', 'none', 900.0))
AFTER_TURN = (('turn', 'right', 0.0), ('arrive', 'none', 500.0))


class TestTransientNav:
  def setup_method(self, method):
    self.nav = TransientNav()
    self.cue_id = 7  # arbitrary: the machine must treat the first value it sees as stale

  def drive(self, specs=FULL_ROUTE, stage: str = '', active: bool = True, cue: bool = False) -> TransientNavState:
    if cue:
      self.cue_id += 1
    return self.nav.update(active, _maneuvers(*specs), self.cue_id, stage)

  def test_starts_off_and_wakes_quiet(self):
    assert self.nav.state == TransientNavState.OFF
    assert self.drive(active=False) == TransientNavState.OFF
    assert self.drive() == TransientNavState.QUIET

  def test_approach_cue_expands(self):
    self.drive(stage='approach')  # sticky pre-existing cue: adopted, not acted on
    assert self.nav.state == TransientNavState.QUIET
    assert self.drive(stage='approach', cue=True) == TransientNavState.APPROACH

  def test_imminent_expands_for_chained_maneuvers(self):
    # chained maneuvers skip the approach stage, so imminent must open the display too
    self.drive()
    assert self.drive(stage='imminent', cue=True) == TransientNavState.APPROACH

  def test_informational_cues_do_not_expand(self):
    self.drive()
    for stage in ('lane', 'digest', 'reroute', 'arrive'):
      assert self.drive(stage=stage, cue=True) == TransientNavState.QUIET, stage

  def test_repeated_stage_without_a_new_cue_does_not_expand(self):
    # audioCueStage is sticky between cues; only the id increment means a cue fired
    self.drive(stage='approach')
    assert self.drive(stage='approach') == TransientNavState.QUIET

  def test_collapses_once_the_maneuver_passes(self):
    self.drive()
    self.drive(stage='approach', cue=True)
    assert self.drive(AFTER_TURN, stage='approach') == TransientNavState.QUIET

  def test_tap_pins_and_unpins_the_quiet_chip(self):
    self.drive()
    self.nav.on_tap()
    assert self.nav.state == TransientNavState.PINNED
    self.nav.on_tap()
    assert self.nav.state == TransientNavState.QUIET

  def test_tap_dismisses_an_approach_until_the_maneuver_passes(self):
    self.drive()
    self.drive(stage='approach', cue=True)
    self.nav.on_tap()
    assert self.nav.state == TransientNavState.QUIET
    # the same maneuver's later cue must not bring the display back
    assert self.drive(stage='imminent', cue=True) == TransientNavState.QUIET
    # but the next maneuver's cue starts fresh
    self.drive(AFTER_TURN)
    assert self.drive(AFTER_TURN, stage='imminent', cue=True) == TransientNavState.APPROACH

  def test_pinned_rides_through_maneuvers_and_cues(self):
    self.drive()
    self.nav.on_tap()
    assert self.drive(stage='approach', cue=True) == TransientNavState.PINNED
    assert self.drive(AFTER_TURN) == TransientNavState.PINNED

  def test_losing_the_route_resets_everything(self):
    self.drive()
    self.nav.on_tap()
    assert self.drive(active=False) == TransientNavState.OFF
    # pinning was a choice about the old route; a new one starts quiet
    assert self.drive() == TransientNavState.QUIET

  def test_signature_tells_apart_identical_consecutive_turns(self):
    # two right turns in a row: the list length is what distinguishes them
    first = (('depart', 'straight', 0.0), ('turn', 'right', 300.0), ('turn', 'right', 600.0), ('arrive', 'none', 900.0))
    second = (('turn', 'right', 0.0), ('turn', 'right', 300.0), ('arrive', 'none', 600.0))
    assert maneuver_signature(_maneuvers(*first)) != maneuver_signature(_maneuvers(*second))
    self.drive(first)
    self.drive(first, stage='imminent', cue=True)
    assert self.nav.state == TransientNavState.APPROACH
    assert self.drive(second) == TransientNavState.QUIET


def _status(state: NavState, allow: bool = True, hud: bool = True):
  return SimpleNamespace(allow_navigation=allow, show_turn_indicator=hud, state=state)


class TestChipMode:
  def test_absent_without_a_route_to_have(self):
    assert chip_mode(_status(NavState.OFFLINE)) == ChipMode.HIDDEN
    assert chip_mode(_status(NavState.NO_DESTINATION)) == ChipMode.HIDDEN

  def test_hidden_when_switched_off(self):
    assert chip_mode(_status(NavState.ACTIVE, allow=False)) == ChipMode.HIDDEN
    assert chip_mode(_status(NavState.ACTIVE, hud=False)) == ChipMode.HIDDEN

  def test_searching_while_a_route_is_pending(self):
    assert chip_mode(_status(NavState.WAITING_FOR_GPS)) == ChipMode.SEARCHING
    assert chip_mode(_status(NavState.COMPUTING)) == ChipMode.SEARCHING

  def test_failures_are_visible(self):
    assert chip_mode(_status(NavState.NO_ROUTE)) == ChipMode.FAILURE

  def test_live_while_routing(self):
    assert chip_mode(_status(NavState.ACTIVE)) == ChipMode.LIVE
