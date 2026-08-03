"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from types import SimpleNamespace

from openpilot.common.params import Params
from openpilot.sunnypilot.navd.constants import LANE_GUIDANCE_ASSIST, LANE_GUIDANCE_DISPLAY
from openpilot.sunnypilot.navd.helpers import lane_change_auto_confirm, lane_change_hint, parse_banner_instructions
from openpilot.sunnypilot.navd.navigation_desires.navigation_desires import NavigationDesires
from openpilot.sunnypilot.navd.navigationd import Navigationd


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


TWO_LANES = [{'active': True, 'directions': ['left']}, {'active': False, 'directions': ['straight']}]


class TestAutoConfirm:
  def test_slip_road_types_confirm_without_lane_data(self):
    assert lane_change_auto_confirm(_hint_progress('off ramp', 'slightRight', 200.0), None)
    assert lane_change_auto_confirm(_hint_progress('merge', 'slightLeft', 200.0), None)

  def test_ambiguous_types_need_a_multi_lane_approach(self):
    for maneuver_type in ('turn', 'fork', 'continue', 'end of road'):
      progress = _hint_progress(maneuver_type, 'left', 200.0)
      assert not lane_change_auto_confirm(progress, None)
      assert not lane_change_auto_confirm(progress, [TWO_LANES[0]])
      assert lane_change_auto_confirm(progress, TWO_LANES)

  def test_uturn_never_confirms(self):
    assert not lane_change_auto_confirm(_hint_progress('turn', 'uturn', 200.0), TWO_LANES)

  def test_lone_maneuver_does_not_confirm(self):
    assert not lane_change_auto_confirm({'all_maneuvers': [{'type': 'arrive', 'modifier': 'none', 'distance': 50.0}]}, None)

  def test_flag_reaches_the_message(self):
    nav = Navigationd()
    msg = nav._build_navigation_message('', None, {'lane_change_direction': 'left', 'lane_change_auto_confirm': True}, True)
    assert msg.navigationd.laneChangeAutoConfirm
    msg = nav._build_navigation_message('', None, {'lane_change_direction': 'left'}, True)
    assert not msg.navigationd.laneChangeAutoConfirm


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
