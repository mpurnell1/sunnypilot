"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pytest

from openpilot.common.params import Params
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigation_helpers import mapbox_integration
from openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration import MapboxIntegration

FAST = {'legs': [{'summary': 'US-101 North'}]}
SCENIC = {'legs': [{'summary': 'CA-1'}]}
NO_LEGS = {'legs': []}


class TestSelectRoute:
  def test_no_preference_takes_the_fastest(self):
    assert MapboxIntegration._select_route([FAST, SCENIC], None) is FAST
    assert MapboxIntegration._select_route([FAST, SCENIC], '') is FAST

  def test_preference_picks_the_matching_alternate(self):
    assert MapboxIntegration._select_route([FAST, SCENIC], 'CA-1') is SCENIC

  def test_unmatched_preference_falls_back_to_the_fastest(self):
    # after a reroute the chosen road may no longer be on offer; the fastest route is the
    # only sane fallback and must not raise
    assert MapboxIntegration._select_route([FAST, SCENIC], 'I-5 South') is FAST

  def test_route_without_legs_cannot_match(self):
    assert MapboxIntegration._select_route([NO_LEGS, SCENIC], 'CA-1') is SCENIC


class TestPreferenceBinding:
  """The stored preference names the destination it was chosen for; any other destination
  must request the fastest route."""

  @pytest.fixture(autouse=True)
  def setup(self, mocker):
    self.params = Params()
    self.params.put('MapboxToken', 'pk.test-token', block=True)
    self.mapbox = MapboxIntegration()
    self.seen_preferences: list = []
    self.seen_pins: list = []

    def fake_generate_route(start, end, token, bearing=None, preference=None, pin=None):
      self.seen_preferences.append(preference)
      self.seen_pins.append(pin)
      return {'steps': [], 'totalDistance': 1.0, 'totalDuration': 1.0, 'geometry': [], 'maxspeed': []}

    mocker.patch.object(self.mapbox, 'generate_route', side_effect=fake_generate_route)
    mocker.patch.object(self.mapbox, 'get_timezone', return_value=None)

  def confirm(self, place_name: str) -> bool:
    destination = {'place_name': place_name, 'latitude': 47.6, 'longitude': -122.1}
    return self.mapbox.request_route(destination, Coordinate(47.5, -122.0)) is not None

  def test_matching_destination_carries_the_preference(self):
    self.params.put('MapboxRoutePreference', {'dest': '-122.1,47.6', 'summary': 'CA-1'}, block=True)
    assert self.confirm('-122.1,47.6')
    assert self.seen_preferences == ['CA-1']

  def test_other_destination_gets_no_preference(self):
    self.params.put('MapboxRoutePreference', {'dest': '-122.1,47.6', 'summary': 'CA-1'}, block=True)
    assert self.confirm('740 E Ventura Blvd')
    assert self.seen_preferences == [None]

  def test_no_stored_preference(self):
    assert self.confirm('-122.1,47.6')
    assert self.seen_preferences == [None]
    assert self.seen_pins == [None]

  def test_a_pinned_preference_carries_its_coordinate(self):
    self.params.put('MapboxRoutePreference', {'dest': '-122.1,47.6', 'summary': 'CA-1', 'via': '-122.05,47.55'}, block=True)
    assert self.confirm('-122.1,47.6')
    assert self.seen_preferences == ['CA-1']
    assert (self.seen_pins[0].longitude, self.seen_pins[0].latitude) == (-122.05, 47.55)


DIRECTIONS_OK = {
  'code': 'Ok',
  'routes': [{'legs': [{'summary': 'CA-1', 'steps': [], 'annotation': {'maxspeed': []}}],
              'distance': 1.0, 'duration': 1.0, 'geometry': {'coordinates': [[-122.0, 47.5], [-122.1, 47.6]]}}],
}


class TestPinnedRequest:
  """A pin still ahead rides the Directions request as a silent via; one behind the car does not."""

  @pytest.fixture(autouse=True)
  def setup(self, mocker):
    self.requests: list = []

    def fake_get_json(what, url, params, timeout):
      self.requests.append((url, dict(params)))
      return DIRECTIONS_OK

    mocker.patch.object(mapbox_integration, '_get_json', side_effect=fake_get_json)

  def test_a_pin_ahead_becomes_a_silent_via(self):
    start, pin, end = Coordinate(47.5, -122.0), Coordinate(47.55, -122.05), Coordinate(47.6, -122.1)
    assert MapboxIntegration.generate_route(start, end, 'pk.test', bearing=90.0, preference='CA-1', pin=pin) is not None
    url, params = self.requests[0]
    assert url.endswith('/-122.0,47.5;-122.05,47.55;-122.1,47.6')
    assert params['waypoints'] == '0;2'
    assert params['alternatives'] == 'false'
    assert params['bearings'] == '90,90;;'

  def test_a_pin_behind_the_car_is_left_out(self):
    start, pin, end = Coordinate(47.58, -122.08), Coordinate(47.55, -122.05), Coordinate(47.6, -122.1)
    assert MapboxIntegration.generate_route(start, end, 'pk.test', bearing=90.0, pin=pin) is not None
    url, params = self.requests[0]
    assert url.endswith('/-122.08,47.58;-122.1,47.6')
    assert 'waypoints' not in params
    assert params['alternatives'] == 'true'
    assert params['bearings'] == '90,90;'
