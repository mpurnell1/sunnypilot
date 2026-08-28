#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Scripted navigation demo player. Feeds the real navigation UI with fake state so every
nav display and cue can be exercised on a parked device, no route or movement required.

The scenarios are timelines of simulated route progress published as real navigationd
messages at 3 Hz. Cue timing is not scripted: each tick drives the production NavAudioCues
instance and the production message builder, so approach and imminent windows, restraint
rules, and message shape are exactly what a drive would produce.

Bench mode (renders the real widgets on the device screen, comma service stopped):
  sudo systemctl stop comma
  cd /data/openpilot
  OPENPILOT_PREFIX=navdemo /usr/local/venv/bin/python openpilot/sunnypilot/tools/nav_demo.py approach
  sudo systemctl start comma

Screenshot mode (same, but writes rotated navdemo_*.png at each labeled moment):
  OPENPILOT_PREFIX=navdemo /usr/local/venv/bin/python openpilot/sunnypilot/tools/nav_demo.py tour --shots

Live mode (publishes only navigationd into the running UI's namespace; the car must be
onroad and the real navigationd process stopped so the socket is free):
  /usr/local/venv/bin/python openpilot/sunnypilot/tools/nav_demo.py approach --live

On a PC the window comes up at the mici 536x240 and the bench drives the mici HUD stack,
so the comma four skin can be exercised with no four on hand (BIG=1 for the 3X stack):
  OPENPILOT_PREFIX=navdemo python openpilot/sunnypilot/tools/nav_demo.py tour --shots
"""
import argparse
import os
import sys
import time
from dataclasses import dataclass, replace
from types import SimpleNamespace

# runnable by path from the repo root, where python puts the script dir on sys.path instead
if not __package__:
  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

import openpilot.cereal.messaging as messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.nav_audio import NavAudioCues
from openpilot.sunnypilot.navd.navigationd import Navigationd

RATE = 3  # Hz, matching navigationd
TICK = 1.0 / RATE

DEST = "1505 N Neil St, Champaign, IL"
DEST_TZ = "America/Chicago"

# lane rows appear once the banner for the maneuver goes full; Mapbox typically flips
# showFull inside the last few hundred meters
LANE_SHOW_M = 400.0

TURN_APPROACH_SPEED = 9.0  # m/s the sim slows to for a corner
CORNER_SLOW_M = 140.0


@dataclass(frozen=True)
class Leg:
  """A stretch of road ending in a maneuver: the maneuver is the leg's destination."""
  length: float       # meters from the previous maneuver to this one
  mtype: str          # Mapbox maneuver type ('turn', 'fork', 'arrive', ...)
  modifier: str       # Mapbox modifier ('right', 'slightLeft', ...)
  instruction: str
  speed: float        # m/s cruise speed while driving this leg
  lanes: tuple = ()   # ((directions, active, activeDirection), ...) shown on the approach


@dataclass(frozen=True)
class RouteSpec:
  depart: tuple       # (type, modifier, instruction) for the maneuver already behind the car
  legs: tuple


@dataclass(frozen=True)
class TickState:
  """One 3 Hz tick of simulated navigation state, everything a message needs."""
  route: RouteSpec | None = None
  leg: int = 0
  dist: float = 0.0        # meters to the end of the current leg
  v: float = 0.0           # m/s
  off_route: float = 4.0   # meters from the route line
  rerouting: bool = False
  arrived: bool = False
  failures: int = 0        # consecutive failed route requests
  gps_ok: bool = True      # the localizer fix, published as msg.valid
  destination: str = DEST  # what the MapboxRoute param should read this tick
  label: str | None = None  # named moment, printed and used as the screenshot name


def _clip(x: float, lo: float, hi: float) -> float:
  return max(lo, min(hi, x))


def _ticks(seconds: float, speedup: float) -> int:
  return max(1, round(seconds * RATE / speedup))


class _Drive:
  """Minimal kinematics: ease toward each leg's speed, slow for corners, stop at the flag."""

  def __init__(self, route: RouteSpec):
    self.route = route
    self.leg = 0
    self.dist = route.legs[0].length
    self.v = route.legs[0].speed

  def advance(self, dt: float) -> bool:
    leg = self.route.legs[self.leg]
    target = leg.speed
    if leg.mtype == 'arrive':
      target = min(target, self.dist / 5.0)
    elif self.dist < CORNER_SLOW_M:
      target = min(target, TURN_APPROACH_SPEED)
    self.v = max(0.0, self.v + _clip(target - self.v, -3.0 * dt, 1.8 * dt))
    self.dist -= self.v * dt
    if self.dist <= 0.0:
      if leg.mtype == 'arrive':
        # the destination is a place to stop, not a leg to complete
        self.dist = 0.5
        self.v = 0.0
        return True
      self.leg += 1
      if self.leg >= len(self.route.legs):
        return False
      self.dist += self.route.legs[self.leg].length
    return True


def _tick_data(state: TickState) -> tuple[str, dict, dict]:
  """The (banner, progress, nav_data) triple navigationd's builder and NavAudioCues expect."""
  route = state.route
  leg = route.legs[state.leg]
  later = route.legs[state.leg + 1:]
  distance_remaining = state.dist + sum(l.length for l in later)
  time_remaining = state.dist / max(leg.speed, 1.0) + sum(l.length / max(l.speed, 1.0) for l in later)

  prev = route.depart if state.leg == 0 else \
    (route.legs[state.leg - 1].mtype, route.legs[state.leg - 1].modifier, route.legs[state.leg - 1].instruction)
  maneuvers = [{'distance': 0.0, 'type': prev[0], 'modifier': prev[1], 'instruction': prev[2]}]
  d = state.dist
  for i, l in enumerate(route.legs[state.leg:]):
    maneuvers.append({'distance': d, 'type': l.mtype, 'modifier': l.modifier, 'instruction': l.instruction})
    j = state.leg + i + 1
    if j < len(route.legs):
      d += route.legs[j].length

  progress = {
    'current_step_idx': state.leg,
    'distance_to_end_of_step': state.dist,
    'current_step': {'distance': leg.length},
    'next_turn': {'maneuver': leg.mtype, 'modifier': leg.modifier, 'instruction': leg.instruction},
    'all_maneuvers': maneuvers,
  }
  nav_data = {
    'upcoming_turn': leg.modifier if state.dist < 250.0 else 'none',
    'arrived': state.arrived,
    'distance_from_route': state.off_route,
    'distance_remaining': distance_remaining,
    'time_remaining': time_remaining,
  }
  if leg.lanes and state.dist < LANE_SHOW_M:
    nav_data['lanes'] = [{'directions': list(dirs), 'active': active, 'activeDirection': active_dir}
                         for dirs, active, active_dir in leg.lanes]
  return leg.instruction, progress, nav_data


def _build_msg(state: TickState, cues: NavAudioCues, stub: SimpleNamespace):
  if state.route is not None:
    banner, progress, nav_data = _tick_data(state)
  else:
    banner, progress, nav_data = '', None, {}
  cues.update(state.route, progress, nav_data, state.v, state.rerouting)
  stub.valid = state.route is not None
  stub.failed_attempts = state.failures
  stub.rerouting = state.rerouting
  # the daemon debounces this over ticks; the demo reads it straight off the drift meters
  stub.off_route = state.route is not None and not state.arrived and state.off_route > 210.0
  # the demo has no geography, so the fix is a fixed point; losing GPS drops it like the daemon's
  stub.last_position = Coordinate(34.22, -119.03) if state.gps_ok else None
  stub.last_bearing = 0.0 if state.gps_ok else None
  stub.route = {'route_id': 1} if state.route is not None else None
  # the production builder, called with a stub in place of the daemon, so the message
  # shape can never drift from what navigationd publishes
  return Navigationd._build_navigation_message(stub, banner, progress, nav_data, state.gps_ok)


# --- scenarios: generators yielding one TickState per 3 Hz tick ---

def scenario_approach(speedup: float = 1.0):
  """The core demo: an 800 m countdown to a right turn, lane row, then the corner itself."""
  route = RouteSpec(
    depart=('depart', 'straight', 'Head north on S State St'),
    legs=(
      Leg(800.0, 'turn', 'right', 'Turn right onto N Neil St', 19.2, lanes=(
        (('straight',), False, ''), (('straight',), False, ''), (('straight', 'right'), True, 'right'))),
      Leg(600.0, 'fork', 'slightLeft', 'Keep left toward I-74 W', 13.4),
      Leg(1200.0, 'arrive', 'none', 'You have arrived at your destination', 17.9),
    ))
  drive = _Drive(route)
  # thresholds sit just past where the real cue windows land at this speed
  labels = [('quiet', 750.0), ('approach', 420.0), ('lanes', 170.0), ('turn', 30.0)]
  past_turn_ticks = 0
  while True:
    label = None
    if drive.leg == 0 and labels and drive.dist <= labels[0][1]:
      label = labels.pop(0)[0]
    elif drive.leg == 1:
      past_turn_ticks += 1
      if past_turn_ticks == _ticks(3.0, speedup):
        label = 'after_turn'
    yield TickState(route=route, leg=drive.leg, dist=drive.dist, v=drive.v, label=label)
    if label == 'after_turn' or not drive.advance(TICK * speedup):
      return


def scenario_reroute(speedup: float = 1.0):
  """Drift off the route, one reroute cue, then a fresh route takes over."""
  route1 = RouteSpec(
    depart=('depart', 'straight', 'Head west on E University Ave'),
    legs=(Leg(1400.0, 'turn', 'right', 'Turn right onto N Prospect Ave', 22.4),
          Leg(900.0, 'arrive', 'none', 'You have arrived', 13.4)))
  route2 = RouteSpec(
    depart=('depart', 'straight', 'Head north on Country Fair Dr'),
    legs=(Leg(1500.0, 'turn', 'left', 'Turn left onto W Springfield Ave', 22.4),
          Leg(700.0, 'arrive', 'none', 'You have arrived', 13.4)))
  dt = TICK * speedup

  drive = _Drive(route1)
  for i in range(_ticks(4.0, speedup)):
    yield TickState(route=route1, leg=drive.leg, dist=drive.dist, v=drive.v,
                    label='on_route' if i == 0 else None)
    drive.advance(dt)

  # veer off: progress along the route freezes while distance from it grows, and after the
  # same few seconds navigationd's reroute counter needs, the recompute begins
  off = 4.0
  over_threshold = 0
  rerouting = False
  reroute_hold = 0
  while reroute_hold < _ticks(3.0, speedup):
    off = min(320.0, off + 9.0 * speedup)
    if off > 210.0:
      over_threshold += 1
    label = None
    if not rerouting and over_threshold == 1:
      label = 'off_route'
    if not rerouting and over_threshold >= _ticks(3.3, speedup):
      rerouting = True
      label = 'reroute'
    if rerouting:
      reroute_hold += 1
    yield TickState(route=route1, leg=drive.leg, dist=drive.dist, v=drive.v,
                    off_route=off, rerouting=rerouting, label=label)

  drive = _Drive(route2)
  for i in range(_ticks(5.0, speedup)):
    yield TickState(route=route2, leg=drive.leg, dist=drive.dist, v=drive.v,
                    label='rerouted' if i == 0 else None)
    drive.advance(dt)


def scenario_arrival(speedup: float = 1.0):
  """Roll up to the destination, the arrive cue, then navigationd's cleanup."""
  route = RouteSpec(
    depart=('turn', 'right', 'Turn right onto N Neil St'),
    legs=(Leg(320.0, 'arrive', 'none', 'You have arrived at 1505 N Neil St', 13.4),))
  drive = _Drive(route)
  arrived = False
  announced = False
  hold = 0
  while hold < _ticks(4.0, speedup):
    if not arrived and drive.dist < 35.0:
      arrived = True
    label = None
    if arrived and not announced:
      label = 'arrive'
      announced = True
    # the trip lingers until the car has actually stopped at the flag
    if arrived and drive.v < 0.7:
      hold += 1
    yield TickState(route=route, leg=drive.leg, dist=drive.dist, v=drive.v, arrived=arrived, label=label)
    drive.advance(TICK * speedup)
  # arrival concludes the trip on its own: the route drops and the destination param clears.
  # The UI notices the cleared param on a 1 Hz wall-time poll, so this hold ignores speedup
  # and the label lands at the end of it, once the indicator has caught up
  cleanup = _ticks(2.0, 1.0)
  for i in range(cleanup):
    yield TickState(route=None, destination='', label='cleared' if i == cleanup - 1 else None)


def scenario_failure(speedup: float = 1.0):
  """A destination with no route: no fix, computing, failing with backoff, then recovery."""
  # the pole stage: a destination is set but the localizer has no fix yet, so the searching
  # flag is a bare pole. The UI loses a confirmed fix on a wall-clock hold, so this stretch
  # ignores speedup and the label lands at its end.
  no_fix = _ticks(3.0, 1.0)
  for i in range(no_fix):
    yield TickState(route=None, gps_ok=False, label='no_fix' if i == no_fix - 1 else None)
  for i in range(_ticks(3.0, speedup)):
    yield TickState(route=None, label='requesting' if i == 0 else None)
  for fails in (1, 2):
    for i in range(_ticks(4.0, speedup)):
      yield TickState(route=None, failures=fails, label=f'failed_{fails}' if i == 0 else None)
  route = RouteSpec(
    depart=('depart', 'straight', 'Head north on S State St'),
    legs=(Leg(1500.0, 'turn', 'right', 'Turn right onto N Neil St', 19.2),
          Leg(500.0, 'arrive', 'none', 'You have arrived', 13.4)))
  drive = _Drive(route)
  for i in range(_ticks(5.0, speedup)):
    yield TickState(route=route, leg=drive.leg, dist=drive.dist, v=drive.v,
                    label='recovered' if i == 0 else None)
    drive.advance(TICK * speedup)


SCENARIOS = {
  'approach': scenario_approach,
  'reroute': scenario_reroute,
  'arrival': scenario_arrival,
  'failure': scenario_failure,
}


def message_stream(names, speedup: float = 1.0, hold_ticks: int = 0):
  """(scenario, state, message) per tick; one cue engine spans the whole run, like the daemon."""
  cues = NavAudioCues()
  stub = SimpleNamespace(valid=False, failed_attempts=0, nav_audio=cues, rerouting=False, off_route=False)
  for name in names:
    last = None
    for state in SCENARIOS[name](speedup):
      yield name, state, _build_msg(state, cues, stub)
      last = state
    for _ in range(hold_ticks):
      held = replace(last, label=None)
      yield name, held, _build_msg(held, cues, stub)


# --- runners ---

def _cue_text(nav) -> str:
  text = f"{nav.audioCueStage}:{nav.audioCueKind}"
  if nav.audioCueDirection != 'none':
    text += f":{nav.audioCueDirection}"
  if nav.audioCueCount:
    text += f":{nav.audioCueCount}"
  return text


def _publish_support(socks, v_ego: float) -> None:
  msg = messaging.new_message('deviceState')
  msg.valid = True
  msg.deviceState.networkType = 'wifi'
  # started plus ignition below lets the real ui.py, run against this prefix on a PC,
  # treat the session as onroad and show its onroad view
  msg.deviceState.started = True
  socks['deviceState'].send(msg.to_bytes())

  msg = messaging.new_message('pandaStates', 1)
  msg.valid = True
  msg.pandaStates[0].pandaType = 'dos'
  msg.pandaStates[0].ignitionLine = True
  socks['pandaStates'].send(msg.to_bytes())

  msg = messaging.new_message('carState')
  msg.valid = True
  msg.carState.vEgo = float(v_ego)
  msg.carState.vEgoCluster = float(v_ego)
  msg.carState.vCruiseCluster = 104.6  # kph, a 65 mph set speed
  socks['carState'].send(msg.to_bytes())

  msg = messaging.new_message('selfdriveState')
  msg.valid = True
  msg.selfdriveState.enabled = True
  msg.selfdriveState.engageable = True
  socks['selfdriveState'].send(msg.to_bytes())

  msg = messaging.new_message('modelV2')
  msg.valid = True
  preds = msg.modelV2.meta.disengagePredictions
  preds.init('brakeDisengageProbs', 1)
  preds.brakeDisengageProbs[0] = 0.02
  preds.init('steerOverrideProbs', 1)
  preds.steerOverrideProbs[0] = 0.05
  socks['modelV2'].send(msg.to_bytes())

  msg = messaging.new_message('carOutput')
  msg.valid = True
  msg.carOutput.actuatorsOutput.torque = 0.12
  socks['carOutput'].send(msg.to_bytes())


def run_ui(names, save_shots: bool, speedup: float, metric: bool, quiet_glyph: bool = False) -> None:
  prefix = os.environ.get('OPENPILOT_PREFIX')
  if not prefix:
    sys.exit('bench mode needs OPENPILOT_PREFIX for msgq and params isolation (or pass --live)')
  os.chdir(BASEDIR)
  # every SubMaster in the UI stack must find its msgq segment in this prefix. uiDebug is
  # left unclaimed: it is the UI's own publication, and holding it would stop a real ui.py
  # from running against this prefix on a PC
  os.makedirs(f"/dev/shm/msgq_{prefix}", exist_ok=True)
  socks = {s: messaging.pub_sock(s) for s in SERVICE_LIST if s != 'uiDebug'}

  params = Params()
  # the real params store always has a DongleId; the isolated prefix store never does
  assert params.get('DongleId') is None, 'params are not isolated, refusing to run'
  params.put_bool('AllowNavigation', True)
  params.put('NavHudMode', 3)
  params.put('NavLaneGuidance', 1)
  params.put('MapboxRoute', DEST)
  params.put('NavDestinationTimezone', DEST_TZ)
  params.put_bool('NavMiciQuietGlyph', quiet_glyph)

  import pyray as rl
  from openpilot.system.ui.lib.application import gui_app, FontWeight
  from openpilot.system.ui.lib.text_measure import measure_text_cached
  gui_app.init_window('nav demo')
  from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
  # the window size picked the skin: the 3X rail-and-banner stack on a big display, the
  # mici corner stack at 536x240
  if gui_app.big_ui():
    from openpilot.selfdrive.ui.sunnypilot.onroad.hud_renderer import HudRendererSP
  else:
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_renderer import HudRendererSP

  ui_state.is_metric = metric
  ui_state.status = UIStatus.ENGAGED
  # the real UI loop polls settings into ui_state; the bench reads them once, after the
  # nav params above are in the isolated store
  ui_state.update_params()
  # the production HUD stack: chip, banner, speed pill reflow, and route summary all hang
  # off HudRendererSP exactly as they do onroad
  hud = HudRendererSP()
  w, h = gui_app.width, gui_app.height
  rect = rl.Rectangle(0, 0, w, h)
  caption_font = gui_app.font(FontWeight.MEDIUM)
  caption_size = 30 if gui_app.big_ui() else 18
  caption_y = h - 44 if gui_app.big_ui() else h - 24

  def draw_scene() -> None:
    # synthetic road, just enough context for the HUD to read as onroad
    rl.clear_background(rl.Color(8, 11, 14, 255))
    gray = rl.Color(150, 158, 165, 170)
    vy = h * 0.52
    rl.draw_line_ex(rl.Vector2(w * 0.12, h), rl.Vector2(w * 0.48, vy), 5, gray)
    rl.draw_line_ex(rl.Vector2(w * 0.88, h), rl.Vector2(w * 0.52, vy), 5, gray)
    path = [rl.Vector2(w * 0.455, vy + 90), rl.Vector2(w * 0.335, h),
            rl.Vector2(w * 0.665, h), rl.Vector2(w * 0.545, vy + 90)]
    rl.draw_triangle_fan(path, 4, rl.Color(23, 196, 110, 80))

  def render_frame(caption: str | None) -> None:
    ui_state.sm.update(0)
    rl.begin_drawing()
    draw_scene()
    hud.render(rect)
    if caption:
      size = measure_text_cached(caption_font, caption, caption_size)
      rl.draw_text_ex(caption_font, caption, rl.Vector2((w - size.x) / 2, caption_y), caption_size, 0,
                      rl.Color(255, 255, 255, 140))
    rl.end_drawing()

  last_cue = ''
  last_cue_id = None
  last_dest = DEST
  last_name = None
  shots: list[str] = []
  next_tick = time.monotonic()

  try:
    for name, state, msg in message_stream(names, speedup, hold_ticks=_ticks(2.0, 1.0)):
      if name != last_name:
        print(f"--- scenario: {name}")
        last_name = name
      if state.destination != last_dest:
        params.put('MapboxRoute', state.destination)
        last_dest = state.destination

      socks['navigationd'].send(msg.to_bytes())
      _publish_support(socks, state.v)

      if last_cue_id is not None and msg.navigationd.audioCueId != last_cue_id:
        last_cue = _cue_text(msg.navigationd)
        print(f"  [cue] {last_cue}")
      last_cue_id = msg.navigationd.audioCueId

      if state.label:
        print(f"  [{name}] {state.label}")
        if save_shots:
          # a labeled state needs real time, not just frames, before the capture shows the
          # settled look: the shared status polls params at 1 Hz, the mici corner walks its
          # alpha on a FirstOrderFilter, and at a run's start the corner yields to the
          # set-speed circle for its 2.5 s persistence window
          settle_end = time.monotonic() + 3.0
          while time.monotonic() < settle_end:
            render_frame(None)
          # raylib resolves the path against the working directory, so keep it bare
          fn = f"navdemo_{name}_{state.label}.png"
          rl.take_screenshot(fn)
          shots.append(fn)

      caption = None if save_shots else f"nav demo | {name}" + (f" | {last_cue}" if last_cue else '')
      next_tick += TICK
      while time.monotonic() < next_tick:
        render_frame(caption)
  finally:
    gui_app.close()

  if shots:
    from openpilot.common.hardware import TICI
    if TICI:
      # the panel framebuffer is portrait; the captures need a 90 degree clockwise rotation
      from PIL import Image
      for fn in shots:
        path = os.path.join(BASEDIR, fn)
        Image.open(path).transpose(Image.ROTATE_270).transpose(Image.ROTATE_180).save(path)
    print("wrote " + " ".join(shots))


def _restore_param(params: Params, key: str, value) -> None:
  if value is None:
    params.remove(key)
  elif isinstance(value, bool):
    params.put_bool(key, value)
  else:
    params.put(key, value)


def run_live(names, speedup: float) -> None:
  if os.environ.get('OPENPILOT_PREFIX'):
    print('warning: OPENPILOT_PREFIX is set, the running UI will not see these messages')
  params = Params()
  saved = {k: params.get(k) for k in ('MapboxRoute', 'AllowNavigation', 'NavDestinationTimezone')}
  try:
    sock = messaging.pub_sock('navigationd')
  except Exception as e:
    sys.exit(f"could not claim the navigationd socket ({e}); stop the real navigationd first")

  params.put_bool('AllowNavigation', True)
  params.put('MapboxRoute', DEST)
  params.put('NavDestinationTimezone', DEST_TZ)

  last_cue_id = None
  last_dest = DEST
  last_name = None
  next_tick = time.monotonic()
  try:
    for name, state, msg in message_stream(names, speedup, hold_ticks=_ticks(2.0, 1.0)):
      if name != last_name:
        print(f"--- scenario: {name}")
        last_name = name
      if state.destination != last_dest:
        params.put('MapboxRoute', state.destination)
        last_dest = state.destination
      sock.send(msg.to_bytes())
      if last_cue_id is not None and msg.navigationd.audioCueId != last_cue_id:
        print(f"  [cue] {_cue_text(msg.navigationd)}")
      last_cue_id = msg.navigationd.audioCueId
      if state.label:
        print(f"  [{name}] {state.label}")
      next_tick += TICK
      delay = next_tick - time.monotonic()
      if delay > 0:
        time.sleep(delay)
  finally:
    for key, value in saved.items():
      _restore_param(params, key, value)


def main() -> None:
  parser = argparse.ArgumentParser(description='scripted navigation demo against the real nav UI')
  parser.add_argument('scenario', nargs='?', default='tour', choices=[*SCENARIOS, 'tour'],
                      help='which script to play (tour chains all of them)')
  parser.add_argument('--shots', action='store_true',
                      help='also write a rotated screenshot at each labeled moment (bench mode only)')
  parser.add_argument('--live', action='store_true',
                      help='publish navigationd into the live namespace for the running UI')
  parser.add_argument('--speedup', type=float, default=1.0,
                      help='sim time multiplier; wall rate stays 3 Hz (default 1.0)')
  parser.add_argument('--metric', action='store_true', help='render metric units in bench mode')
  parser.add_argument('--quiet-glyph', action='store_true',
                      help='set NavMiciQuietGlyph so the mici quiet state shows the faint glyph (bench mode only)')
  args = parser.parse_args()
  if args.live and (args.shots or args.quiet_glyph):
    parser.error('--shots and --quiet-glyph need bench mode, drop --live')

  names = list(SCENARIOS) if args.scenario == 'tour' else [args.scenario]
  if args.live:
    run_live(names, args.speedup)
  else:
    run_ui(names, args.shots, args.speedup, args.metric, args.quiet_glyph)


if __name__ == '__main__':
  main()
