"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import requests
from urllib.parse import quote

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog


class MapboxIntegration:
  def __init__(self):
    self.params = Params()

  def get_public_token(self) -> str:
    token: str = self.params.get('MapboxToken', return_default=True)
    return token

  def set_destination(self, postvars, current_lon, current_lat, bearing=None) -> tuple[dict, bool]:
    """Returns the postvars and whether a usable route was stored.

    Geocoding and directions are separate API calls and either can fail on its own, so the
    caller is told about the route rather than just the address: a destination that geocodes
    but produces no route has not been accepted and must be retried.
    """
    if 'latitude' in postvars and 'longitude' in postvars:
      return postvars, self.nav_confirmed(postvars, current_lon, current_lat, bearing)

    addr = postvars['place_name']
    if not addr:
      return postvars, False

    token = self.get_public_token()
    if not token:
      cloudlog.error("navd: geocoding skipped, no MapboxToken set")
      return postvars, False

    url = f'https://api.mapbox.com/geocoding/v5/mapbox.places/{quote(addr)}.json?access_token={token}&limit=1&proximity={current_lon},{current_lat}'
    try:
      response = requests.get(url, timeout=5)
      if response.status_code == 200:
        features = response.json()['features']
        if features:
          longitude, latitude = features[0]['geometry']['coordinates']
          # resolved_name is the human label for recents; place_name stays the raw destination
          # string because the route preference is matched against it
          postvars.update({'latitude': latitude, 'longitude': longitude, 'name': addr,
                           'resolved_name': features[0].get('place_name', '')})
          return postvars, self.nav_confirmed(postvars, current_lon, current_lat, bearing)
        cloudlog.warning("navd: geocoding found no match for destination %r", addr)
      else:
        cloudlog.error("navd: geocoding failed with HTTP %d for destination %r", response.status_code, addr)
    except requests.RequestException as e:
      # Broad exception to handle network errors like no internet without crashing navd process.
      cloudlog.warning("navd: geocoding request failed for destination %r: %s", addr, e)
    except (ValueError, KeyError, IndexError) as e:
      # a 200 with an unexpected body would otherwise take navd down
      cloudlog.error("navd: could not parse geocoding response for destination %r: %s", addr, e)
    return postvars, False

  def nav_confirmed(self, postvars, start_lon, start_lat, bearing=None) -> bool:
    if not postvars:
      return False

    latitude = float(postvars['latitude'])
    longitude = float(postvars['longitude'])

    # a route preference exists only if the destination page stored one, and it names the
    # destination it was chosen for: a destination set any other way must get the fastest route
    preference = None
    stored = self.params.get('MapboxRoutePreference')
    if isinstance(stored, dict) and stored.get('dest') == postvars.get('place_name'):
      preference = stored.get('summary')

    token = self.get_public_token()
    route_data = self.generate_route(start_lon, start_lat, longitude, latitude, token, bearing, preference)
    if not route_data:
      # storing an empty route here would discard a working one on a failed reroute, and would
      # read as an accepted destination that navd then has no reason to recompute
      cloudlog.error("navd: no route stored for destination %r, keeping any previous route", postvars.get('name'))
      return False

    data: dict = {'navData': {'current': {'latitude': latitude, 'longitude': longitude}, 'route': route_data}}
    self.params.put('MapboxSettings', data)

    # the device clock is GPS-synced UTC with no system timezone, so the ETA readout needs the
    # destination's zone. Cleared on failure: a zone left over from an earlier trip is worse
    # than the UI's device-local fallback
    tzid = self.get_timezone(longitude, latitude, token)
    if tzid:
      self.params.put('NavDestinationTimezone', tzid)
    else:
      self.params.remove('NavDestinationTimezone')
    return True

  def search_places(self, query: str, proximity_lon=None, proximity_lat=None, limit: int = 5) -> list[dict] | None:
    """Forward geocoding for the destination page: several candidates, not navd's single best match.

    Returns None when the request itself failed, [] when Mapbox found nothing, so the caller
    can tell an offline device from a bad query.
    """
    token = self.get_public_token()
    if not token or not query:
      return None

    url = f'https://api.mapbox.com/geocoding/v5/mapbox.places/{quote(query)}.json'
    params: dict = {'access_token': token, 'limit': limit}
    if proximity_lon is not None and proximity_lat is not None:
      params['proximity'] = f'{proximity_lon},{proximity_lat}'
    try:
      response = requests.get(url, params=params, timeout=10)
      if response.status_code != 200:
        cloudlog.error("destinationd: search failed with HTTP %d for %r", response.status_code, query)
        return None
      return [
        {'name': feature['place_name'], 'longitude': feature['geometry']['coordinates'][0],
         'latitude': feature['geometry']['coordinates'][1]}
        for feature in response.json()['features']
      ]
    except requests.RequestException as e:
      cloudlog.warning("destinationd: search request failed for %r: %s", query, e)
    except (ValueError, KeyError, IndexError) as e:
      cloudlog.error("destinationd: could not parse search response for %r: %s", query, e)
    return None

  def preview_routes(self, start_lon, start_lat, end_lon, end_lat) -> list[dict] | None:
    """Route alternates with live and typical durations, for the pick-a-route step.

    steps=true is required even though the steps are discarded: without it the leg summary
    comes back empty, and the summary is what identifies the chosen alternate later.
    """
    token = self.get_public_token()
    if not token:
      return None

    params = {
      'access_token': token,
      'geometries': 'geojson',
      'steps': 'true',
      'overview': 'false',
      'alternatives': 'true',
    }
    url = f'https://api.mapbox.com/directions/v5/mapbox/driving-traffic/{start_lon},{start_lat};{end_lon},{end_lat}'
    try:
      response = requests.get(url, params=params, timeout=10)
      if response.status_code != 200:
        cloudlog.error("destinationd: route preview failed with HTTP %d", response.status_code)
        return None
      data = response.json()
      if data.get('code') != 'Ok':
        cloudlog.error("destinationd: route preview returned no route (code=%s)", data.get('code'))
        return None
      return [
        {
          'summary': (route.get('legs') or [{}])[0].get('summary', ''),
          'distance': route['distance'],
          'duration': route['duration'],
          'durationTypical': route.get('duration_typical', route['duration']),
        }
        for route in data['routes']
      ]
    except requests.RequestException as e:
      cloudlog.warning("destinationd: route preview request failed: %s", e)
    except (ValueError, KeyError, IndexError) as e:
      cloudlog.error("destinationd: could not parse route preview response: %s", e)
    return None

  # Mapbox's public timezone boundary tileset, queried per accepted route rather than shipping
  # a coordinate-to-zone dataset on the device
  @staticmethod
  def get_timezone(lon, lat, token) -> str | None:
    url = f'https://api.mapbox.com/v4/examples.4ze9z6tv/tilequery/{lon},{lat}.json'
    try:
      response = requests.get(url, params={'access_token': token}, timeout=5)
      if response.status_code != 200:
        cloudlog.error("navd: timezone lookup failed with HTTP %d", response.status_code)
        return None
      features = response.json()['features']
      if features:
        return features[0]['properties']['TZID']
      cloudlog.warning("navd: no timezone found at %s,%s", lon, lat)
    except requests.RequestException as e:
      cloudlog.warning("navd: timezone request failed: %s", e)
    except (ValueError, KeyError, IndexError) as e:
      cloudlog.error("navd: could not parse timezone response: %s", e)
    return None

  @staticmethod
  def _select_route(routes: list, preference: str | None) -> dict:
    """The fastest route unless the driver picked an alternate by its leg summary.

    Preferences survive reroutes on purpose: mid-trip the preferred road is usually still on
    offer, and when it no longer applies the fastest route is the right fallback anyway.
    """
    if preference:
      for route in routes:
        legs = route.get('legs') or []
        if legs and legs[0].get('summary') == preference:
          cloudlog.warning("navd: using preferred route %r", preference)
          return route
      cloudlog.warning("navd: preferred route %r not offered, using the fastest", preference)
    return routes[0]

  @staticmethod
  def generate_route(start_lon, start_lat, end_lon, end_lat, token, bearing=None, preference=None) -> dict | None:
    if not token:
      cloudlog.error("navd: route generation skipped, no MapboxToken set")
      return None

    params = {
      'access_token': token,
      'geometries': 'geojson',
      'steps': 'true',
      'overview': 'full',
      'annotations': 'maxspeed',
      # alternates cost nothing extra per request and are what lets a stored route preference
      # actually pick the road the driver chose on the destination page
      'alternatives': 'true',
      'banner_instructions': 'true',
    }
    if bearing is not None:
      params['bearings'] = f'{int((bearing + 360) % 360):.0f},90;'

    try:
      # driving-traffic: durations include live traffic, so the ETA is an estimate rather
      # than the free-flow floor the plain driving profile returns
      url = f'https://api.mapbox.com/directions/v5/mapbox/driving-traffic/{start_lon},{start_lat};{end_lon},{end_lat}'
      response = requests.get(url, params=params, timeout=5)
      if response.status_code != 200:
        cloudlog.error("navd: directions failed with HTTP %d", response.status_code)
      data = response.json() if response.status_code == 200 else {}
    except requests.RequestException as e:
      cloudlog.warning("navd: directions request failed: %s", e)
      return None
    except ValueError as e:
      cloudlog.error("navd: could not parse directions response: %s", e)
      return None

    routes = data.get('routes') if data else None

    if data.get('code') != 'Ok' or not routes or not routes[0].get('legs'):
      if data:
        cloudlog.error("navd: directions returned no usable route (code=%s)", data.get('code'))
      return None

    route = MapboxIntegration._select_route(routes, preference)
    leg = route['legs'][0]

    steps = [
      {
        'maneuver': step['maneuver']['type'],
        'instruction': step['maneuver']['instruction'],
        'distance': step['distance'],
        'duration': step['duration'],
        'location': {'longitude': step['maneuver']['location'][0], 'latitude': step['maneuver']['location'][1]},
        'modifier': step['maneuver'].get('modifier', 'none'),
        'bannerInstructions': step['bannerInstructions'],
      }
      for step in leg['steps']
    ]

    maxspeed = [{'speed': item['speed'], 'unit': item['unit']} for item in leg['annotation']['maxspeed'] if 'speed' in item]

    return {
      'steps': steps,
      'totalDistance': route['distance'],
      'totalDuration': route['duration'],
      'geometry': [{'longitude': coord[0], 'latitude': coord[1]} for coord in route['geometry']['coordinates']],
      'maxspeed': maxspeed,
    }
