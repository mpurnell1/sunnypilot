"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The destination contract, transport-agnostic: one implementation the LAN page
(destinationd) and the athena RPC methods (navd/athena_methods.py) both call, so the
two fronts cannot drift. Methods return plain dicts and raise ApiError for every
refusal; each transport maps ApiError to its own error shape (HTTP status, JSON-RPC
error). The gate is a callable so each transport supplies its own vehicle check with
identical semantics: sets and settings writes only offroad or at a standstill,
cancel any time.
"""
from collections.abc import Callable

from openpilot.common.params import Params
from openpilot.sunnypilot.navd.helpers import coordinate_from_param
from openpilot.sunnypilot.navd.navigation_helpers.destination_store import DestinationStore
from openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration import MapboxIntegration

STANDSTILL_SPEED = 0.5  # m/s


class ApiError(Exception):
  def __init__(self, message: str, status: int = 400):
    super().__init__(message)
    self.status = status


class DestinationAPI:
  def __init__(self, params: Params | None = None, store: DestinationStore | None = None,
               mapbox: MapboxIntegration | None = None, can_set: Callable[[], bool] = lambda: True,
               offroad: Callable[[], bool] = lambda: True, source: str = "destinationd"):
    self.params = params or Params()
    self.store = store or DestinationStore(self.params)
    self.mapbox = mapbox or MapboxIntegration()
    self.can_set = can_set
    self.offroad = offroad
    self.source = source

  def status(self) -> dict:
    return {
      "destination": self.store.active_destination(),
      "navEnabled": self.params.get_bool("AllowNavigation"),
      "tokenSet": bool(self.mapbox.get_public_token()),
      "offroad": self.offroad(),
      "canSet": self.can_set(),
      "favorites": self.store.favorites(),
      "recents": self.store.recents(),
    }

  def search(self, text: str) -> dict:
    text = str(text).strip()
    if not text:
      raise ApiError("empty query")
    position = coordinate_from_param("LastGPSPositionLLK", self.params)
    proximity = (position.longitude, position.latitude) if position else (None, None)
    results = self.mapbox.search_places(text, *proximity)
    if results is None:
      raise ApiError("search failed, check the connection and Mapbox token", status=502)
    return {"results": results}

  def routes(self, end_lon, end_lat) -> dict:
    try:
      end_lon, end_lat = float(end_lon), float(end_lat)
    except (TypeError, ValueError):
      raise ApiError("lon and lat are required") from None
    position = coordinate_from_param("LastGPSPositionLLK", self.params)
    if position is None:
      # without a last known fix there is no start point to route from; the destination can
      # still be set, navd will route once the car has a position
      raise ApiError("no known device position", status=409)
    routes = self.mapbox.preview_routes(position.longitude, position.latitude, end_lon, end_lat)
    if routes is None:
      raise ApiError("route preview failed", status=502)
    return {"routes": routes}

  def navigate(self, dest, name="", summary="") -> dict:
    # settable while driving (Matt's ruling, 2026-08-27): nav desires need the driver's
    # blinker and torque, so a route swap is display-only; the gate survives on settings
    if not str(dest or "").strip():
      raise ApiError("dest is required")
    self.store.set_destination(str(dest), name=str(name or ""), route_summary=str(summary or ""))
    return self.status()

  def cancel(self) -> dict:
    self.store.clear_destination(source=self.source)
    return self.status()

  def favorites_action(self, action, name="", dest="", kind=None, summary="") -> dict:
    if action == "set" and str(dest or "").strip():
      self.store.set_favorite(str(name or ""), str(dest), kind=kind, summary=str(summary or ""))
    elif action == "remove":
      self.store.remove_favorite(str(name or ""), kind=kind)
    else:
      raise ApiError("action must be set (with dest) or remove")
    return {"favorites": self.store.favorites()}

  # the remote settings scope is display and audio only: NavDesiresAllowed and the assist
  # level stay on-device so steering influence consent happens in the car
  def settings_view(self) -> dict:
    p = self.params
    return {
      "navHudMode": p.get("NavHudMode", return_default=True),
      "navAudio": p.get("NavigationAudio", return_default=True),
      "laneGuidanceDisplay": (p.get("NavLaneGuidance", return_default=True) or 0) >= 1,
      "recompute": p.get_bool("MapboxRecompute"),
      # write-only by design: set or not set is all a client ever learns of the token
      "tokenSet": bool(self.mapbox.get_public_token()),
    }

  @staticmethod
  def _valid_index(value, upper: int) -> bool:
    # bool is an int, and True must not slip through as mode 1
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= upper

  def apply_settings(self, body: dict) -> dict:
    if not self.can_set():
      # a passenger may cancel or reroute mid-drive, not reconfigure
      raise ApiError("settings can only be changed while parked", status=409)

    p = self.params
    if "navHudMode" in body:
      if not self._valid_index(body["navHudMode"], 3):
        raise ApiError("navHudMode must be an integer 0 to 3")
      p.put("NavHudMode", body["navHudMode"], block=True)
    if "navAudio" in body:
      if not self._valid_index(body["navAudio"], 2):
        raise ApiError("navAudio must be an integer 0 to 2")
      p.put("NavigationAudio", body["navAudio"], block=True)
    if "laneGuidanceDisplay" in body:
      # the client only flips display on and off; an assist level set on the device survives
      # while the toggle stays on, and off is honestly off
      current = p.get("NavLaneGuidance", return_default=True) or 0
      if not body["laneGuidanceDisplay"]:
        p.put("NavLaneGuidance", 0, block=True)
      elif current < 1:
        p.put("NavLaneGuidance", 1, block=True)
    if "recompute" in body:
      p.put_bool("MapboxRecompute", bool(body["recompute"]), block=True)
    if "token" in body:
      # write-only and never cleared from here: an empty submit is a no-op, not a wipe
      token = str(body["token"]).strip()
      if token:
        p.put("MapboxToken", token, block=True)
    return self.settings_view()
