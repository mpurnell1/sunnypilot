"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.navd.helpers import parse_banner_instructions
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
