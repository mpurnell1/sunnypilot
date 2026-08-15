"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

destinationd: the destination page, served from the device for phones on the same
LAN or hotspot (http://<device-ip>:5050, hotspot default http://192.168.43.1:5050).

Search, route choice, favorites, and recents talk to Mapbox from the device with the
public token; the token itself is never sent to the browser. The daemon runs whenever
navigation is enabled so a passenger can cancel guidance mid-drive, but setting a
destination is only allowed offroad or at a standstill. Away from the car, athena's
setNavDestination and tools/send_nav_destination.py remain the path in.
"""
import argparse
import json
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.navd.helpers import coordinate_from_param
from openpilot.sunnypilot.navd.navigation_helpers.destination_store import DestinationStore
from openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration import MapboxIntegration

DEFAULT_PORT = 5050
STANDSTILL_SPEED = 0.5  # m/s
PAGE_PATH = Path(__file__).parent / "assets" / "destination_page.html"


@lru_cache(maxsize=1)
def _page_bytes() -> bytes:
  return PAGE_PATH.read_bytes()


class VehicleMonitor(threading.Thread):
  """Caches whether setting a destination is allowed right now.

  Handler threads must not share a SubMaster, so one thread owns it and the handlers read
  plain attributes. Onroad with no fresh carState the answer is conservative: deny.
  """

  def __init__(self, params: Params):
    super().__init__(daemon=True, name="destinationd_vehicle")
    self.params = params
    self.sm = messaging.SubMaster(['carState'])
    self.offroad = True
    self.moving = False

  @property
  def can_set(self) -> bool:
    return self.offroad or not self.moving

  def run(self):
    while True:
      self.sm.update(1000)
      self.offroad = self.params.get_bool("IsOffroad")
      if self.offroad:
        self.moving = False
      elif self.sm.alive['carState']:
        self.moving = self.sm['carState'].vEgo > STANDSTILL_SPEED
      else:
        self.moving = True


def _json_response(payload: dict, status: int = 200) -> tuple[int, bytes, str]:
  return status, json.dumps(payload).encode(), "application/json"


class DestinationHandler(BaseHTTPRequestHandler):
  protocol_version = "HTTP/1.1"

  # path -> allowed methods; everything else is a 404 so the server never doubles as a file host
  _routes = {
    "/": ("GET", "HEAD"),
    "/api/status": ("GET",),
    "/api/search": ("GET",),
    "/api/routes": ("GET",),
    "/api/navigate": ("POST",),
    "/api/cancel": ("POST",),
    "/api/favorites": ("POST",),
  }

  def _send(self, status: int, body: bytes, content_type: str) -> None:
    self.send_response(status)
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    if self.command != "HEAD":
      self.wfile.write(body)

  def _read_json(self) -> dict | None:
    length = int(self.headers.get("Content-Length", 0))
    try:
      payload = json.loads(self.rfile.read(length)) if length else {}
    except ValueError:
      return None
    return payload if isinstance(payload, dict) else None

  def _handle_status(self) -> tuple[int, bytes, str]:
    server: DestinationHTTPServer = self.server  # type: ignore[assignment]
    return _json_response({
      "destination": server.store.active_destination(),
      "navEnabled": server.params.get_bool("AllowNavigation"),
      "tokenSet": bool(server.mapbox.get_public_token()),
      "offroad": server.vehicle.offroad,
      "canSet": server.vehicle.can_set,
      "favorites": server.store.favorites(),
      "recents": server.store.recents(),
    })

  def _handle_search(self, query: dict) -> tuple[int, bytes, str]:
    server: DestinationHTTPServer = self.server  # type: ignore[assignment]
    text = (query.get("q", [""])[0]).strip()
    if not text:
      return _json_response({"error": "empty query"}, status=400)
    position = coordinate_from_param("LastGPSPositionLLK", server.params)
    proximity = (position.longitude, position.latitude) if position else (None, None)
    results = server.mapbox.search_places(text, *proximity)
    if results is None:
      return _json_response({"error": "search failed, check the connection and Mapbox token"}, status=502)
    return _json_response({"results": results})

  def _handle_routes(self, query: dict) -> tuple[int, bytes, str]:
    server: DestinationHTTPServer = self.server  # type: ignore[assignment]
    try:
      end_lon = float(query["lon"][0])
      end_lat = float(query["lat"][0])
    except (KeyError, ValueError, IndexError):
      return _json_response({"error": "lon and lat are required"}, status=400)
    position = coordinate_from_param("LastGPSPositionLLK", server.params)
    if position is None:
      # without a last known fix there is no start point to route from; the destination can
      # still be set, navd will route once the car has a position
      return _json_response({"error": "no known device position"}, status=409)
    routes = server.mapbox.preview_routes(position.longitude, position.latitude, end_lon, end_lat)
    if routes is None:
      return _json_response({"error": "route preview failed"}, status=502)
    return _json_response({"routes": routes})

  def _handle_navigate(self) -> tuple[int, bytes, str]:
    server: DestinationHTTPServer = self.server  # type: ignore[assignment]
    body = self._read_json()
    if body is None or not str(body.get("dest", "")).strip():
      return _json_response({"error": "dest is required"}, status=400)
    if not server.vehicle.can_set:
      return _json_response({"error": "destination can only be set while parked"}, status=409)
    server.store.set_destination(str(body["dest"]), name=str(body.get("name", "")),
                                 route_summary=str(body.get("summary", "")))
    return self._handle_status()

  def _handle_cancel(self) -> tuple[int, bytes, str]:
    server: DestinationHTTPServer = self.server  # type: ignore[assignment]
    server.store.clear_destination()
    return self._handle_status()

  def _handle_favorites(self) -> tuple[int, bytes, str]:
    server: DestinationHTTPServer = self.server  # type: ignore[assignment]
    body = self._read_json()
    action = body.get("action") if body else None
    if action == "set" and str(body.get("dest", "")).strip():
      server.store.set_favorite(str(body.get("name", "")), str(body["dest"]), kind=body.get("kind"),
                                summary=str(body.get("summary", "")))
    elif action == "remove":
      server.store.remove_favorite(str(body.get("name", "")), kind=body.get("kind"))
    else:
      return _json_response({"error": "action must be set (with dest) or remove"}, status=400)
    return _json_response({"favorites": server.store.favorites()})

  def _dispatch_request(self) -> None:
    parsed = urlparse(self.path)
    allowed = self._routes.get(parsed.path)

    try:
      if allowed is None:
        result = _json_response({"error": "not found"}, status=404)
      elif self.command not in allowed:
        result = _json_response({"error": "method not allowed"}, status=405)
      elif parsed.path == "/":
        result = (200, _page_bytes(), "text/html; charset=utf-8")
      elif parsed.path == "/api/status":
        result = self._handle_status()
      elif parsed.path == "/api/search":
        result = self._handle_search(parse_qs(parsed.query))
      elif parsed.path == "/api/routes":
        result = self._handle_routes(parse_qs(parsed.query))
      elif parsed.path == "/api/navigate":
        result = self._handle_navigate()
      elif parsed.path == "/api/cancel":
        result = self._handle_cancel()
      else:  # /api/favorites
        result = self._handle_favorites()
    except Exception as e:
      cloudlog.exception("destinationd: unhandled error handling %s", self.path)
      result = _json_response({"error": "exception", "message": f"{type(e).__name__}: {e}"}, status=500)

    self._send(*result)

  def do_GET(self) -> None:
    self._dispatch_request()

  def do_HEAD(self) -> None:
    self._dispatch_request()

  def do_POST(self) -> None:
    self._dispatch_request()

  def log_message(self, format: str, *args: object) -> None:  # noqa: A002  # stdlib override
    # silence per-request access logging; errors are logged explicitly in _dispatch_request
    pass


class DestinationHTTPServer(ThreadingHTTPServer):
  daemon_threads = True
  allow_reuse_address = True
  params: Params
  store: DestinationStore
  mapbox: MapboxIntegration
  vehicle: VehicleMonitor


def make_server(host: str = "0.0.0.0", port: int = DEFAULT_PORT, params: Params | None = None,
                vehicle=None) -> DestinationHTTPServer:
  server = DestinationHTTPServer((host, port), DestinationHandler)
  server.params = params or Params()
  server.store = DestinationStore(server.params)
  server.mapbox = MapboxIntegration()
  server.vehicle = vehicle if vehicle is not None else VehicleMonitor(server.params)
  return server


def main():
  parser = argparse.ArgumentParser(description="sunnypilot destination page server")
  parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to listen on")
  parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
  args = parser.parse_args()

  server = make_server(args.host, args.port)
  server.vehicle.start()
  cloudlog.warning("destinationd: serving on %s:%d", args.host, args.port)
  server.serve_forever()


if __name__ == "__main__":
  main()
