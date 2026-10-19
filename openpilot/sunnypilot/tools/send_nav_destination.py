#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Send a navigation destination to a device over comma's athena backend (requires comma prime).

The destination can be a Google Maps share link, a "lat,lon" pair, or a plain address:
  send_nav_destination.py "https://maps.app.goo.gl/AbCdEf" --dongle-id 0123456789abcdef
  send_nav_destination.py "32.7767,-96.7970" --dongle-id 0123456789abcdef
  send_nav_destination.py "Taco Bell, Dallas TX" --dongle-id 0123456789abcdef

Authentication uses a comma account JWT (https://jwt.comma.ai), read from --token,
the COMMA_JWT environment variable, or the repo's cached login (tools/lib/auth.py).
The script only needs `requests`, so it also runs standalone, e.g. under Termux as
an Android share target via Termux:Widget or Tasker.
"""
import argparse
import os
import re
import sys
from urllib.parse import unquote_plus

import requests

ATHENA_HOST = "https://athena.comma.ai"
# resolving share links without a browser UA can bounce to an interstitial instead of the map URL
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

COORD = r"(-?\d{1,3}(?:\.\d+)?)"
# a !3d..!4d.. pair is the pin of the shared place itself; @lat,lon is only the viewport center
PIN_RE = re.compile(rf"!3d{COORD}!4d{COORD}")
QUERY_RE = re.compile(rf"[?&](?:q|query|destination)={COORD}(?:,|%2C){COORD}")
VIEWPORT_RE = re.compile(rf"/@{COORD},{COORD}")
PLACE_RE = re.compile(r"/(?:place|search)/([^/@?]+)")
LATLON_RE = re.compile(rf"^\s*{COORD}\s*,\s*{COORD}\s*$")


def resolve_url(url: str) -> str:
  resp = requests.get(url, headers={"User-Agent": USER_AGENT}, allow_redirects=True, timeout=10, stream=True)
  resp.close()
  return resp.url


def parse_destination(dest: str) -> dict:
  """Turn user input into setNavDestination params: a pin when one can be found, text otherwise."""
  if m := LATLON_RE.match(dest):
    return {"latitude": float(m.group(1)), "longitude": float(m.group(2))}

  if not re.match(r"https?://", dest):
    return {"place_name": dest}

  url = resolve_url(dest)
  # a maps URL often carries both a pin and the place's name; the pin sets the point
  # and the name rides along so the device can label the recents entry
  name = unquote_plus(m.group(1)) if (m := PLACE_RE.search(url)) else None
  for pattern in (PIN_RE, QUERY_RE, VIEWPORT_RE):
    if m := pattern.search(url):
      pin = {"latitude": float(m.group(1)), "longitude": float(m.group(2))}
      if name:
        pin["place_name"] = name
      return pin
  if name:
    return {"place_name": name}

  raise ValueError(f"could not find a destination in {url}")


def get_token(cli_token: str | None) -> str:
  token = cli_token or os.environ.get("COMMA_JWT")
  if not token:
    try:
      from openpilot.tools.lib.auth_config import get_token as get_cached_token
      token = get_cached_token()
    except ImportError:
      pass
  if not token:
    raise SystemExit("no auth token: pass --token, set COMMA_JWT, or log in with tools/lib/auth.py")
  return token


def send_destination(dongle_id: str, token: str, params: dict) -> None:
  payload = {"method": "setNavDestination", "params": params, "jsonrpc": "2.0", "id": 0}
  resp = requests.post(f"{ATHENA_HOST}/{dongle_id}", json=payload, headers={"Authorization": f"JWT {token}"}, timeout=30)
  resp.raise_for_status()
  result = resp.json()
  if result.get("result", {}).get("success") != 1:
    raise SystemExit(f"device rejected destination: {result}")


def main():
  parser = argparse.ArgumentParser(description="Send a nav destination to a comma device via athena")
  parser.add_argument("destination", help="Google Maps share link, 'lat,lon', or address text")
  parser.add_argument("--dongle-id", default=os.environ.get("COMMA_DONGLE_ID"), help="device dongle ID (or COMMA_DONGLE_ID)")
  parser.add_argument("--token", default=None, help="comma account JWT (or COMMA_JWT)")
  args = parser.parse_args()

  if not args.dongle_id:
    parser.error("--dongle-id is required (or set COMMA_DONGLE_ID)")

  params = parse_destination(args.destination)
  send_destination(args.dongle_id, get_token(args.token), params)

  sent = params.get("place_name") or f"{params['latitude']},{params['longitude']}"
  print(f"destination sent: {sent}")


if __name__ == "__main__":
  try:
    main()
  except (requests.RequestException, ValueError) as e:
    print(f"error: {e}", file=sys.stderr)
    sys.exit(1)
