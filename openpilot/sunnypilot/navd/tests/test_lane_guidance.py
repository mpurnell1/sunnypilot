"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from types import SimpleNamespace

from openpilot.common.params import Params
from openpilot.sunnypilot.navd.constants import LANE_GUIDANCE_ASSIST, LANE_GUIDANCE_DISPLAY
from openpilot.sunnypilot.navd.helpers import Coordinate, compose_banner_text, lane_change_auto_confirm, lane_change_hint, parse_banner_instructions
from openpilot.sunnypilot.navd.navigation_desires.navigation_desires import NavigationDesires
from openpilot.sunnypilot.navd.navigationd import HINT_STABLE_CYCLES, Navigationd


def _banner(distance: float, lanes: list | None = None) -> dict:
  banner = {'distanceAlongGeometry': distance, 'primary': {'text': 'Turn right', 'type': 'turn', 'modifier': 'right'}}
  if lanes is not None:
    banner['sub'] = {'components': [{'type': 'lane', **lane} for lane in lanes] + [{'type': 'text', 'text': 'noise'}]}
  return banner


class TestLaneParsing:
  def test_lanes_normalized(self):
    banners = [_banner(400, lanes=[
      {'active': False, 'directions': ['straight']},
      {'active': True, 'directions': ['straight', 'slight right'], 'active_direction': 'slight right'},
      {'active': False, 'directions': ['uturn']},
    ])]
    parsed = parse_banner_instructions(banners, distance_to_maneuver=100)
    assert parsed['showFull']
    assert parsed['lanes'] == [
      {'active': False, 'directions': ['straight']},
      {'active': True, 'directions': ['straight', 'slightRight'], 'activeDirection': 'slightRight'},
      {'active': False, 'directions': ['uturn']},
    ]

  def test_no_sub_banner_has_no_lanes(self):
    parsed = parse_banner_instructions([_banner(400)], distance_to_maneuver=100)
    assert 'lanes' not in parsed

  def test_far_banner_is_not_full(self):
    parsed = parse_banner_instructions([_banner(400)], distance_to_maneuver=800)
    assert not parsed['showFull']


class TestLanePublishing:
  def test_lanes_reach_the_message(self):
    nav = Navigationd()
    nav_data = {'lanes': [{'active': True, 'directions': ['left', 'straight'], 'activeDirection': 'left'},
                          {'active': False, 'directions': ['right']}]}
    msg = nav._build_navigation_message('', None, nav_data, True)
    lanes = msg.navigationd.lanes
    assert len(lanes) == 2
    assert list(lanes[0].directions) == ['left', 'straight']
    assert lanes[0].active
    assert lanes[0].activeDirection == 'left'
    assert not lanes[1].active
    assert lanes[1].activeDirection == ''

  def test_no_lanes_publishes_empty(self):
    nav = Navigationd()
    msg = nav._build_navigation_message('', None, {}, True)
    assert len(msg.navigationd.lanes) == 0
    assert msg.navigationd.laneChangeDirection == 'none'


def _hint_progress(maneuver_type: str, modifier: str, distance: float) -> dict:
  return {'all_maneuvers': [
    {'type': 'depart', 'modifier': 'none', 'distance': 40.0, 'instruction': ''},
    {'type': maneuver_type, 'modifier': modifier, 'distance': distance, 'instruction': ''},
  ]}


class TestLaneChangeHint:
  def test_sides(self):
    assert lane_change_hint(_hint_progress('off ramp', 'slightRight', 200.0), 30.0) == 'right'
    assert lane_change_hint(_hint_progress('turn', 'left', 200.0), 30.0) == 'left'
    assert lane_change_hint(_hint_progress('turn', 'uturn', 200.0), 30.0) == 'left'

  def test_window_scales_with_speed(self):
    progress = _hint_progress('off ramp', 'slightRight', 700.0)
    assert lane_change_hint(progress, 35.0) == 'right'
    assert lane_change_hint(progress, 10.0) == 'none'

  def test_roundabouts_and_straight_are_excluded(self):
    assert lane_change_hint(_hint_progress('roundabout', 'slightRight', 100.0), 30.0) == 'none'
    assert lane_change_hint(_hint_progress('continue', 'straight', 100.0), 30.0) == 'none'

  def test_lone_maneuver_has_no_hint(self):
    assert lane_change_hint({'all_maneuvers': [{'type': 'arrive', 'modifier': 'none', 'distance': 50.0}]}, 30.0) == 'none'


class TestAutoConfirm:
  def test_slip_road_topology_confirms(self):
    assert lane_change_auto_confirm(_hint_progress('off ramp', 'slightRight', 200.0))
    assert lane_change_auto_confirm(_hint_progress('merge', 'slightLeft', 200.0))

  def test_turns_never_confirm(self):
    # near a turn the blinker means the turn: an auto lane change would aim at a lane
    # that may not exist, so only the wheel nudge starts one, however many lanes the map shows
    for maneuver_type in ('turn', 'fork', 'continue', 'end of road'):
      assert not lane_change_auto_confirm(_hint_progress(maneuver_type, 'left', 200.0))

  def test_uturn_never_confirms(self):
    assert not lane_change_auto_confirm(_hint_progress('turn', 'uturn', 200.0))

  def test_lone_maneuver_does_not_confirm(self):
    assert not lane_change_auto_confirm({'all_maneuvers': [{'type': 'arrive', 'modifier': 'none', 'distance': 50.0}]})

  def test_flag_reaches_the_message(self):
    nav = Navigationd()
    msg = nav._build_navigation_message('', None, {'lane_change_direction': 'left', 'lane_change_auto_confirm': True}, True)
    assert msg.navigationd.laneChangeAutoConfirm
    msg = nav._build_navigation_message('', None, {'lane_change_direction': 'left'}, True)
    assert not msg.navigationd.laneChangeAutoConfirm


# the road names are real banner texts from the 2026-08-03 drives, where they appeared
# on the turn card with no action attached
class TestBannerWording:
  def test_road_names_get_their_action_back(self):
    assert compose_banner_text('South 5th Street / I 55 Business', 'turn', 'right') == 'Turn right onto South 5th Street / I 55 Business'
    assert compose_banner_text('Exit 179 I-57', 'off ramp', 'slight right') == 'Take Exit 179 I-57'
    assert compose_banner_text('I 72 East / US 51 North', 'merge', 'slight left') == 'Merge left onto I 72 East / US 51 North'
    assert compose_banner_text('North Neil Street', 'turn', 'slight left') == 'Bear left onto North Neil Street'

  def test_action_only_texts_pass_through(self):
    assert compose_banner_text('Turn right', 'turn', 'right') == 'Turn right'
    assert compose_banner_text('Bear left', 'turn', 'slight left') == 'Bear left'
    assert compose_banner_text('Your destination is on the right', 'arrive', 'right') == 'Your destination is on the right'

  def test_forks_and_unnumbered_exits(self):
    assert compose_banner_text('I-74 West', 'fork', 'left') == 'Keep left toward I-74 West'
    assert compose_banner_text('J. David Jones Parkway / IL 29', 'off ramp', 'right') == 'Take the right exit onto J. David Jones Parkway / IL 29'

  def test_uturns_and_roundabouts(self):
    assert compose_banner_text('Main Street', 'turn', 'uturn') == 'Make a U-turn onto Main Street'
    assert compose_banner_text('Main Street', 'roundabout', 'slight right') == 'At the roundabout, exit onto Main Street'

  def test_parse_composes_the_primary_text(self):
    banners = [{'distanceAlongGeometry': 400, 'primary': {'text': 'South 4th Street', 'type': 'turn', 'modifier': 'left'}}]
    parsed = parse_banner_instructions(banners, distance_to_maneuver=100)
    assert parsed['maneuverPrimaryText'] == 'Turn left onto South 4th Street'


class TestHintStability:
  def test_a_flapping_direction_never_publishes(self):
    nav = Navigationd()
    published = [nav._stable_hint(h) for h in ['left', 'right'] * 5]
    assert set(published) == {'none'}

  def test_a_steady_direction_publishes_after_the_dwell(self):
    nav = Navigationd()
    published = [nav._stable_hint('left') for _ in range(HINT_STABLE_CYCLES + 1)]
    assert published[:HINT_STABLE_CYCLES - 1] == ['none'] * (HINT_STABLE_CYCLES - 1)
    assert published[HINT_STABLE_CYCLES - 1:] == ['left', 'left']

  def test_a_cleared_hint_publishes_immediately(self):
    nav = Navigationd()
    for _ in range(HINT_STABLE_CYCLES):
      nav._stable_hint('left')
    assert nav._stable_hint('none') == 'none'


def _trusted_progress(distance_from_route: float) -> dict:
  return {
    'distance_from_route': distance_from_route,
    'current_step': None,
    'next_turn': None,
    'current_maxspeed': (0, 'kmh'),
    'all_maneuvers': [
      {'type': 'depart', 'modifier': 'none', 'distance': 40.0, 'instruction': ''},
      {'type': 'off ramp', 'modifier': 'slightRight', 'distance': 100.0, 'instruction': ''},
    ],
    'current_step_idx': 0,
    'distance_to_end_of_step': 10.0,
    'distance_remaining': 1000.0,
    'time_remaining': 60.0,
  }


class TestHintTrust:
  def _nav(self, distance_from_route: float) -> Navigationd:
    nav = Navigationd()
    nav.allow_navigation = True
    nav.lane_guidance = LANE_GUIDANCE_ASSIST
    nav.route = {'steps': [{}, {}]}
    nav.last_position = Coordinate(32.7767, -96.797)
    nav.nav_instructions.get_route_progress = lambda lat, lon: _trusted_progress(distance_from_route)
    return nav

  def _run(self, nav: Navigationd) -> dict:
    for _ in range(HINT_STABLE_CYCLES + 1):
      _, _, nav_data = nav._update_navigation()
    return nav_data

  def test_hints_publish_on_a_trusted_route(self):
    nav_data = self._run(self._nav(5.0))
    assert nav_data['lane_change_direction'] == 'right'
    assert nav_data['lane_change_auto_confirm']

  def test_hints_are_suppressed_off_route(self):
    nav_data = self._run(self._nav(500.0))
    assert nav_data['lane_change_direction'] == 'none'
    assert not nav_data['lane_change_auto_confirm']

  def test_hints_are_suppressed_while_reroutes_fail(self):
    nav = self._nav(5.0)
    nav.failed_attempts = 1
    nav_data = self._run(nav)
    assert nav_data['lane_change_direction'] == 'none'
    assert not nav_data['lane_change_auto_confirm']


class TestAssistGate:
  def test_hint_requires_assist_mode_and_valid_message(self):
    desires = NavigationDesires()
    msg = desires.sm['navigationd']

    Params().put("NavLaneGuidance", LANE_GUIDANCE_DISPLAY, block=True)
    desires.param_counter = -1
    desires.update_params()
    assert not desires.lane_assist
    assert desires.lane_change_hint() == 'none'

    Params().put("NavLaneGuidance", LANE_GUIDANCE_ASSIST, block=True)
    desires.param_counter = -1
    desires.update_params()
    assert desires.lane_assist
    # message is not valid yet, so still gated
    assert not msg.valid
    assert desires.lane_change_hint() == 'none'

  def test_hint_requires_the_auto_confirm_flag(self):
    desires = NavigationDesires()
    desires.lane_assist = True
    desires.sm = {'navigationd': SimpleNamespace(valid=True, laneChangeDirection='left', laneChangeAutoConfirm=False)}
    assert desires.lane_change_hint() == 'none'
    desires.sm = {'navigationd': SimpleNamespace(valid=True, laneChangeDirection='left', laneChangeAutoConfirm=True)}
    assert desires.lane_change_hint() == 'left'
