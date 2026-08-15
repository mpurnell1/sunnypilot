"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The transient navigation state machine: one state machine, two skins (the 3X rail today,
the mici corner later). It decides how much of the navigation display is showing; what
each state looks like is the renderers' business.

Approach timing is deliberately not re-derived here. navigationd's cue engine already
interpolates its windows against speed and knows about chained maneuvers and crawl
restraint, so the machine edge-detects its cues (audioCueId increments once per cue) and
expands on the approach and imminent stages.
"""
from enum import IntEnum

from openpilot.selfdrive.ui.sunnypilot.nav_status import NavState


class TransientNavState(IntEnum):
  OFF = 0       # nothing to run: no loaded route, or the nav HUD is switched off
  QUIET = 1     # route active, between maneuvers: only the hint chip shows
  APPROACH = 2  # auto-expanded for the leading maneuver; collapses once it passes
  PINNED = 3    # driver's choice: the expanded skin stays up across maneuvers


# the stages that mean "a maneuver is near"; lane/digest/reroute/arrive cues inform
# without being worth a takeover of the display
EXPAND_STAGES = ('approach', 'imminent')


# the chip doubles as the status indicator: absent = no route to have, dimmed = searching,
# live = routing, and route failures get a treatment of their own
class ChipMode(IntEnum):
  HIDDEN = 0
  SEARCHING = 1
  FAILURE = 2
  LIVE = 3


def chip_mode(status) -> ChipMode:
  if not status.allow_navigation or not status.show_turn_indicator or \
      status.state in (NavState.OFFLINE, NavState.NO_DESTINATION):
    return ChipMode.HIDDEN
  if status.state in (NavState.WAITING_FOR_GPS, NavState.COMPUTING):
    return ChipMode.SEARCHING
  if status.state == NavState.NO_ROUTE:
    return ChipMode.FAILURE
  return ChipMode.LIVE


# allManeuvers[0] is the step being driven, whose maneuver is already behind the car; the turn
# that lies ahead is the second entry. Near the destination the 'arrive' step can be the only
# one left, and that one is still worth showing.
def pick_upcoming_maneuver(maneuvers) -> tuple[str, str, float] | None:
  if len(maneuvers) > 1:
    m = maneuvers[1]
  elif len(maneuvers) == 1 and maneuvers[0].type == 'arrive':
    m = maneuvers[0]
  else:
    return None
  return m.type, m.modifier, m.distance


def maneuver_signature(maneuvers) -> tuple[int, str, str] | None:
  """Identity of the upcoming maneuver, stable for as long as it is being approached.

  The list shrinks by one each time a step is passed and is rebuilt on a reroute, so its
  length tells apart consecutive maneuvers of the same type and modifier.
  """
  m = pick_upcoming_maneuver(maneuvers)
  return None if m is None else (len(maneuvers), m[0], m[1])


class TransientNav:
  def __init__(self):
    self.state = TransientNavState.OFF
    self._last_cue_id: int | None = None
    self._signature: tuple | None = None
    self._dismissed: tuple | None = None

  def update(self, active: bool, maneuvers, cue_id: int, cue_stage: str) -> TransientNavState:
    if not active:
      self.state = TransientNavState.OFF
      self._last_cue_id = None
      self._signature = None
      self._dismissed = None
      return self.state

    # pinning is a per-route choice, so regaining a route starts quiet
    if self.state == TransientNavState.OFF:
      self.state = TransientNavState.QUIET

    signature = maneuver_signature(maneuvers)
    if signature != self._signature:
      # the maneuver being shown has passed, or a reroute rebuilt the list
      self._signature = signature
      self._dismissed = None
      if self.state == TransientNavState.APPROACH:
        self.state = TransientNavState.QUIET

    if self._last_cue_id is None:
      # the cue fields are sticky, so the first observed value may be long stale: adopt
      # its id without acting on it
      self._last_cue_id = cue_id
    elif cue_id != self._last_cue_id:
      self._last_cue_id = cue_id
      if cue_stage in EXPAND_STAGES and self.state == TransientNavState.QUIET \
          and signature is not None and signature != self._dismissed:
        self.state = TransientNavState.APPROACH
    return self.state

  def on_tap(self) -> None:
    """Tap pins the quiet chip open and collapses either expanded skin; a dismissed
    approach stays down until its maneuver passes."""
    if self.state == TransientNavState.QUIET:
      self.state = TransientNavState.PINNED
    elif self.state == TransientNavState.PINNED:
      self.state = TransientNavState.QUIET
    elif self.state == TransientNavState.APPROACH:
      self.state = TransientNavState.QUIET
      self._dismissed = self._signature
