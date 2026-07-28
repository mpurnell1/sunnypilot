"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""


class NAV_RETRY:
  """ Spacing for route requests that failed.

  navigationd runs at 3Hz and re-evaluates the destination every iteration, so an
  unsatisfiable request (bad address, bad token, no network) must not be retried at the
  loop rate: each attempt costs a Mapbox geocoding call and possibly a directions call.
  """
  BASE_SECONDS = 10.0  # first retry, then doubling
  MAX_SECONDS = 300.0  # ceiling, so a drive that regains signal still recovers on its own


class NAV_CV:
  """ These distances are expected in meters format and convert to desired format """
  SHORT_DISTANCE_METERS = 200.0
  QUARTER_MILE = 402.336
  POINT_ONE_MILE = 160.9344
  METERS_TO_KILO = 1000  # divide n by this
  METERS_TO_MILE = 1609.344  # divide n by this
  METERS_TO_FEET = 3.280839895  # multiply n by this
