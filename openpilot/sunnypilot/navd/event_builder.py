"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.cereal import custom, messaging
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL

from openpilot.sunnypilot.navd.constants import NAV_CV


class EventBuilder:
  # a one-shot alert is re-added for a moment so a single dropped frame can't swallow it
  STATUS_ALERT_FRAMES = int(0.5 / DT_MDL)

  def __init__(self):
    self._counter: int = -1
    self._enabled: bool = False
    self._params = Params()
    self._gps_valid: bool = False
    self._route_valid: bool = False
    self._gps_frames: int = 0
    self._route_frames: int = 0

  @staticmethod
  def _build_banner_message(metric: bool, nav_msg):
    m = nav_msg.allManeuvers[1] if len(nav_msg.allManeuvers) > 1 else nav_msg.allManeuvers[0]
    banner = m.instruction

    if metric:
      dist = f'{m.distance / NAV_CV.METERS_TO_KILO:.1f} km,'
      if m.distance < NAV_CV.SHORT_DISTANCE_METERS:
        dist = f'{int(m.distance)}m,'
    else:
      dist = f'{m.distance / NAV_CV.METERS_TO_MILE:.1f} mi,'
      if m.distance < NAV_CV.QUARTER_MILE:
        dist = f'{round((m.distance * NAV_CV.METERS_TO_FEET) / 50) * 50}ft,'

    if m.type == 'arrive' or m.type == 'depart' or 'Your destination' in banner:
      base_msg = banner
    elif banner.startswith(('Continue', 'Drive', 'Head')):
      base_msg = f'For {dist} {banner}'
    elif 'Turn' in banner or 'Take' in banner or 'Make' in banner:
      base_msg = f'In {dist} {banner}'
    else:
      base_msg = f'For {dist} Continue on {banner}'

    return base_msg

  @staticmethod
  def _get_turning_message(upcoming_turn):
    turn_messages = {
      'left': 'Turning Left, Make sure to nudge the wheel',
      'right': 'Turning Right, Make sure to nudge the wheel',
      'slightLeft': 'Keeping Left',
      'slightRight': 'Keeping Right',
      'sharpLeft': 'Sharp Left Turn',
      'sharpRight': 'Sharp Right Turn',
      'straight': 'Continuing Straight',
      'uturn': 'U-Turn Ahead',
    }
    return turn_messages.get(upcoming_turn, f"Upcoming {upcoming_turn.replace('_', ' ').title()}")

  @staticmethod
  def build_navigation_events(sm: messaging.SubMaster, metric=True) -> list:
    nav_msg = sm['navigationd']
    # a route can be valid before any maneuvers have been computed, so both are required to build a banner
    if not nav_msg.valid or not len(nav_msg.allManeuvers):
      return []

    banner_message = EventBuilder._build_banner_message(metric, nav_msg)

    if nav_msg.upcomingTurn != 'none':
      banner_message = EventBuilder._get_turning_message(nav_msg.upcomingTurn)

    return [{
      'name': custom.OnroadEventSP.EventName.navigationBanner,
      'message': banner_message,
    }]

  def build_status_events(self, sm: messaging.SubMaster) -> list:
    """One-shot alerts for the two preconditions navigation silently waits on.

    navigationd publishes msg.valid straight from the localizer, and sets navigationd.valid
    only once a usable route is loaded. Until the localizer has a position navigationd does
    not even read the destination param, so surfacing both transitions makes it obvious
    whether nav is actually working or still waiting on a GPS fix.
    """
    gps_valid = bool(sm.valid.get('navigationd', False))
    route_valid = bool(sm['navigationd'].valid)

    if gps_valid and not self._gps_valid:
      self._gps_frames = self.STATUS_ALERT_FRAMES
    if route_valid and not self._route_valid:
      self._route_frames = self.STATUS_ALERT_FRAMES
    self._gps_valid, self._route_valid = gps_valid, route_valid

    events = []
    if self._gps_frames > 0:
      self._gps_frames -= 1
      events.append({
        'name': custom.OnroadEventSP.EventName.navigationGpsAcquired,
        'message': 'GPS location acquired',
      })
    if self._route_frames > 0:
      self._route_frames -= 1
      events.append({
        'name': custom.OnroadEventSP.EventName.navigationRouteActive,
        'message': 'Navigation active',
      })
    return events

  def update(self, sm: messaging.SubMaster) -> list:
    self._counter += 1
    if self._counter % int(3.0 / DT_MDL) == 0:
      self._enabled = self._params.get("NavEvents", return_default=True)

    # tracked even while disabled, so re-enabling doesn't replay a stale transition
    status_events = self.build_status_events(sm)

    if not self._enabled:
      # drop any in-flight one-shot rather than banking it until the toggle comes back on
      self._gps_frames = 0
      self._route_frames = 0
      return []

    return status_events + self.build_navigation_events(sm)
