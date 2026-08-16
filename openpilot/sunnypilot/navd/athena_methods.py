"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The destination contract over comma's athena connection, for clients away from the
car (comma prime carries the tunnel; auth is the comma account JWT, which belongs in
an app, never a browser). Additive by design: upstream setNavDestination is untouched
and these methods reuse the same handlers as the LAN page, so the two transports
cannot drift. Registration happens in athenad, and sunnylinkd shares the dispatcher,
so the methods answer on both relays.

Gating parity with the page: setting a destination needs offroad or standstill,
cancel works any time. athenad has no vehicle thread, so the gate here is a one-shot
carState read with the same conservative deny when no fresh reading exists.
"""
import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.sunnypilot.navd.navigation_helpers.destination_api import ApiError, DestinationAPI, STANDSTILL_SPEED

CARSTATE_TIMEOUT_MS = 1000


def _offroad_now() -> bool:
  return Params().get_bool("IsOffroad")


def _can_set_now() -> bool:
  """The page gate's semantics without its long-lived thread: offroad always allows,
  onroad needs one fresh carState at standstill, and no reading is a deny."""
  if _offroad_now():
    return True
  sock = messaging.sub_sock('carState', timeout=CARSTATE_TIMEOUT_MS)
  try:
    msg = messaging.recv_one(sock)
  finally:
    del sock
  return msg is not None and msg.carState.vEgo <= STANDSTILL_SPEED


def _api() -> DestinationAPI:
  return DestinationAPI(can_set=_can_set_now, offroad=_offroad_now, source="athena")


def getNavStatus() -> dict:
  return _api().status()


def listDestinations() -> dict:
  api = _api()
  return {"favorites": api.store.favorites(), "recents": api.store.recents()}


def setDestination(dest: str = "", name: str = "", summary: str = "") -> dict:
  api = _api()
  # the page cannot be reached at all while navigation is disabled; the honest mirror of
  # that here is a refusal, not a destination navd will silently never read
  if not api.params.get_bool("AllowNavigation"):
    raise ApiError("navigation is disabled on the device", status=409)
  return api.navigate(dest, name=name, summary=summary)


def cancelRoute() -> dict:
  return _api().cancel()


def register(dispatcher) -> None:
  for fn in (getNavStatus, listDestinations, setDestination, cancelRoute):
    dispatcher[fn.__name__] = fn
