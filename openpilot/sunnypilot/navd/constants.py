"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""


class BannerMode:
  OFF = 0
  INCREMENTAL = 1
  ALWAYS = 2


# NavLaneGuidance modes
LANE_GUIDANCE_OFF = 0
LANE_GUIDANCE_DISPLAY = 1
LANE_GUIDANCE_ASSIST = 2


class NAV_BANNER:
  """Incremental banners: show near each distance milestone rather than continuously.

  Thresholds are metres to the upcoming maneuver, coarse far out and tightening near the
  turn, so the prompts feel like a nav app instead of a permanent overlay.
  """
  THRESHOLDS_M = (3000.0, 1500.0, 800.0, 400.0, 150.0, 50.0)
  SHOW_SECONDS = 4.0
  # a maneuver's distance only grows when a new one becomes current, so a jump this large
  # means the previous turn was passed and the milestone sequence restarts
  NEW_MANEUVER_JUMP_M = 50.0


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
