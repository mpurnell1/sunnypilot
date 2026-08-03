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
          postvars.update({'latitude': latitude, 'longitude': longitude, 'name': addr})
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

    token = self.get_public_token()
    route_data = self.generate_route(start_lon, start_lat, longitude, latitude, token, bearing)
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
  def generate_route(start_lon, start_lat, end_lon, end_lat, token, bearing=None) -> dict | None:
    if not token:
      cloudlog.error("navd: route generation skipped, no MapboxToken set")
      return None

    params = {
      'access_token': token,
      'geometries': 'geojson',
      'steps': 'true',
      'overview': 'full',
      'annotations': 'maxspeed',
      'alternatives': 'false',
      'banner_instructions': 'true',
    }
    if bearing is not None:
      params['bearings'] = f'{int((bearing + 360) % 360):.0f},90;'

    try:
      response = requests.get(f'https://api.mapbox.com/directions/v5/mapbox/driving/{start_lon},{start_lat};{end_lon},{end_lat}', params=params, timeout=5)
      if response.status_code != 200:
        cloudlog.error("navd: directions failed with HTTP %d", response.status_code)
      data = response.json() if response.status_code == 200 else {}
    except requests.RequestException as e:
      cloudlog.warning("navd: directions request failed: %s", e)
      return None
    except ValueError as e:
      cloudlog.error("navd: could not parse directions response: %s", e)
      return None

    routes = data['routes'] if data else None
    legs = routes[0]['legs'] if routes else None

    if data.get('code') != 'Ok' or not routes or not legs:
      if data:
        cloudlog.error("navd: directions returned no usable route (code=%s)", data.get('code'))
      return None

    route = routes[0]
    leg = legs[0]

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
