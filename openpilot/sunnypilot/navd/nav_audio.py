"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Audio cue selection for navigation. Decides *when* a prompt is owed and *what* code it
carries; rendering (tones or Morse) belongs to soundd. The vocabulary:

  L/R turn, SL/SR slight, HL/HR sharp, U u-turn, ML/MR merge, XL/XR off-ramp,
  KL/KR keep (fork), O<n> roundabout nth exit, CL/CR lane change,
  QRX rerouting, AR arrived. Digest cues append a mileage digit, e.g. 'R 5'.

Restraint rules, in order of importance: a maneuver that needs no action ('continue
straight') gets no cue, no cue ever plays twice, and prompts other than imminent/reroute/
arrive hold off below a crawl speed rather than chirping through parking lots.
"""
import re

from numpy import interp

from openpilot.sunnypilot.navd.helpers import ROUNDABOUT_TYPES

# metres to the maneuver at which each stage fires, against m/s. Approach spans roughly a
# quarter to half mile at road speeds; imminent lands around the "start braking" point
STAGE_SPEED_BP = [4.5, 13.4, 22.4, 31.3]  # 10/30/50/70 mph
APPROACH_DIST = [150.0, 300.0, 500.0, 800.0]
IMMINENT_DIST = [40.0, 80.0, 130.0, 200.0]

# below this the driver is crawling and already scanning; deferrable stages stay quiet
CRAWL_SPEED = 4.5  # m/s, ~10 mph

# maneuvers closer together than the approach point plus this margin chain: the approach
# prompt would land on top of the previous turn, so only the imminent stage plays
CHAIN_MARGIN = 150.0  # m

DIGEST_MILE = 1609.344  # m; gaps longer than this earn a 'code + miles' digest

MODIFIER_CODES = {
  'uturn': 'U', 'left': 'L', 'right': 'R',
  'slightLeft': 'SL', 'slightRight': 'SR',
  'sharpLeft': 'HL', 'sharpRight': 'HR',
}
# these types override the turn letter with their own prefix + side
TYPE_PREFIXES = {'off ramp': 'X', 'merge': 'M', 'fork': 'K'}
SIDES = {'L': ('uturn', 'left', 'slightLeft', 'sharpLeft'), 'R': ('right', 'slightRight', 'sharpRight')}

ORDINAL_EXIT_RE = re.compile(r'(\d+)\w*\s+exit')


def maneuver_code(maneuver_type: str, modifier: str, instruction: str = '') -> str:
  """The vocabulary code for a maneuver, or '' for ones that need no prompt."""
  if 'exit ro' in maneuver_type:  # 'exit roundabout'/'exit rotary': the entry cue covered it
    return ''
  if any(t in maneuver_type for t in ROUNDABOUT_TYPES):
    exit_num = ORDINAL_EXIT_RE.search(instruction)
    return f'O{exit_num.group(1)}' if exit_num else 'O'
  for prefix_type, prefix in TYPE_PREFIXES.items():
    if prefix_type in maneuver_type:
      side = next((s for s, mods in SIDES.items() if modifier in mods), None)
      # a merge or fork with no stated side is a follow-the-road non-event
      return prefix + side if side else ''
  if maneuver_type in ('continue', 'new name'):
    # the road bends or changes name, which helpers.py renders as a non-maneuver; only the
    # u-turn is real, and Mapbox files divided-road u-turns under 'continue'
    return 'U' if modifier == 'uturn' else ''
  if maneuver_type in ('arrive', 'depart', 'notification'):
    return ''
  return MODIFIER_CODES.get(modifier, '')


class NavAudioCues:
  """Tracks route progress and yields at most one cue per update.

  Eligible cues that lose the priority race simply stay eligible: at 3 Hz the runner-up
  plays a cycle later, so nothing needs a queue.
  """

  def __init__(self):
    self.code: str = ''
    self.stage: str = ''
    self.cue_id: int = 0

    self._route = None
    self._fired: set = set()
    self._reroute_armed: bool = True
    self._arrived: bool = False

  def _fire(self, code: str, stage: str, key: tuple | None = None) -> None:
    self.code = code
    self.stage = stage
    self.cue_id = (self.cue_id + 1) & 0xffffffff
    if key is not None:
      self._fired.add(key)

  def _reset_route(self, route) -> None:
    self._route = route
    self._fired = set()
    self._arrived = False

  def update(self, route, progress: dict | None, nav_data: dict, v_ego: float, rerouting: bool) -> None:
    # a recompute rebuilds the route, so step indices — and every fired key — start over
    if route is not self._route:
      self._reset_route(route)

    if progress is None or route is None:
      return

    # one QRX per excursion: re-arms only once the car is back on the route
    if rerouting:
      if self._reroute_armed:
        self._reroute_armed = False
        self._fire('QRX', 'reroute')
        return
    else:
      self._reroute_armed = True

    if nav_data.get('arrived') and not self._arrived:
      self._arrived = True
      self._fire('AR', 'arrive')
      return

    next_turn = progress['next_turn']
    if next_turn is None:
      return
    nt_idx = progress['current_step_idx'] + 1
    code = maneuver_code(next_turn['maneuver'], next_turn['modifier'], next_turn['instruction'])
    distance = progress['distance_to_end_of_step']
    crawling = v_ego < CRAWL_SPEED

    if code:
      imminent_at = float(interp(v_ego, STAGE_SPEED_BP, IMMINENT_DIST))
      approach_at = float(interp(v_ego, STAGE_SPEED_BP, APPROACH_DIST))

      if distance <= imminent_at and (nt_idx, 'imminent') not in self._fired:
        # a late approach after this would only echo it
        self._fired.add((nt_idx, 'approach'))
        self._fire(code, 'imminent', (nt_idx, 'imminent'))
        return

      if distance <= approach_at and (nt_idx, 'approach') not in self._fired and not crawling:
        step_len = progress['current_step']['distance']
        if step_len < approach_at + CHAIN_MARGIN:
          # chained to the previous maneuver: the imminent cue alone carries it
          self._fired.add((nt_idx, 'approach'))
        else:
          self._fire(code, 'approach', (nt_idx, 'approach'))
          return

    hint = nav_data.get('lane_change_direction', 'none')
    if hint in ('left', 'right') and (nt_idx, 'lane', hint) not in self._fired and not crawling:
      self._fire('C' + hint[0].upper(), 'lane', (nt_idx, 'lane', hint))
      return

    # over a mile of quiet ahead earns one 'code + miles' digest; being far from the
    # maneuver already guarantees this plays early in the step
    if code and not crawling and distance > DIGEST_MILE and (nt_idx, 'digest') not in self._fired:
      miles = int(min(9, max(1, round(distance / DIGEST_MILE))))
      self._fire(f'{code} {miles}', 'digest', (nt_idx, 'digest'))
