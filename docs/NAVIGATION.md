# Navigation

sunnypilot navigation is a driving aid: it shows and speaks turn-by-turn guidance while
you drive the car. It is not point-to-point autonomy. The route never steers the car,
changes lanes, or takes an exit on its own; every maneuver is yours to make, with the
same attention driving always demands.

This implementation builds on the navd work of **discountchubbs**, whose navigation
daemon is the foundation everything here extends. If you find this feature useful,
that groundwork is why it exists.

## What it does

- A quiet chip in the onroad UI carries the route state: absent means no route, a glyph
  with a distance means routing. While searching, the destination flag raises in stages:
  a bare pole until GPS has a fix, pole and banner while the route is requested, and red
  if requests are failing. It expands into a banner as a maneuver approaches and gets
  out of the way after.
- The display is honest about being lost: off the route, the glyph dims and the
  distance disappears rather than counting down to a turn you are not approaching;
  while rerouting, the searching flag returns until the new route lands.
- Audio cues, if enabled, sound as maneuvers approach. The sound tells you what and
  when; the screen tells you where.
- The arrival pill shows remaining time, distance, and arrival time.
- While navigating, the route's speed limit can fill in the speed limit sign when
  neither the car nor map data knows one. Route data can be stale; posted signs win.
- Optional, off by default, and always driver-confirmed: navigation can suggest turns
  and lane changes to the driving model, but only after your own blinker or steering
  input agrees. Nothing acts without a driver signal.

## On the comma four

The four's screen is small and its UI keeps itself out of the way, so navigation there
is a corner, not a card:

- Between maneuvers the screen shows nothing (a faint hint glyph is available behind
  the `NavMiciQuietGlyph` parameter for those who want it). Audio carries the street
  names; the corner carries the shape of the turn.
- As a maneuver approaches, a glyph, the distance, and a small lane row fade into the
  top-left corner, in the slot the set-speed circle uses. They fade back out once the
  turn is made. Alerts and the set-speed circle take the slot with priority.
- Tap the corner to pin it as a persistent corner across maneuvers; tap again to let
  it breathe. There is no hold-to-cancel on the four: cancel from the phone page,
  athena, or by toggling navigation off in settings.
- The same status language applies: the searching flag with its stages, red when
  requests fail, a dimmed glyph when off route.

Setup on the four is deliberately one switch: settings has a single **navigation**
toggle, and everything else (destinations, HUD and audio choices, the Mapbox token)
lives on the phone page the device serves once that toggle is on. The page has a real
keyboard and room for descriptions; the car has one decision to make. Options that
influence steering never appear on the page, so consent for them happens in the car.

## Setup

1. Enable navigation: on the 3X, set a Mapbox token in Settings and enable
   **Allow Navigation** under Settings, Navigation. On the four, turn on the
   **navigation** toggle in settings, then set the token from the phone page's
   Settings section.
2. Set a destination:
   - From a phone on the same network or the device hotspot, open
     `http://<device-ip>:5050`. Search, compare routes with live traffic, and go.
   - From anywhere, comma prime users can send a destination through athena
     (`tools/send_nav_destination.py`, or the richer RPC methods below).
   - From the device, pick a saved favorite in Settings, Navigation.
3. Route choice is yours: the destination page previews alternates with live and
   typical times. A favorite can be bound to a route you always want; a bound favorite
   starts navigating on that route in one tap.

## Away from the car (comma prime / athena)

The destination page only works on the car's network. Away from it, the same contract
rides the websocket the device already keeps open to `athena.comma.ai` (this assumes a
comma prime subscription; whether the tunnel works without one is untested). Clients
POST JSON-RPC to `https://athena.comma.ai/<dongleId>` with an `Authorization: JWT
<token>` header, using a comma account token from https://jwt.comma.ai.

That token is full access to the device, so it belongs in an app or a script you run
yourself, never in a web page: a browser cannot keep it secret, and this fork never
serves or logs it anywhere. The token check is comma's; the device additionally answers
only to accounts paired with it.

The fork registers five additive methods next to the stock `setNavDestination` (which
is untouched, so comma connect and the CLI sender keep working):

- `getNavStatus()`: the page's status payload, including whether a set is allowed now.
- `listDestinations()`: favorites and recents, same shapes as the page.
- `setDestination(dest, name, summary)`: set a destination, optionally with a chosen
  route summary, exactly like tapping a route card on the page. Refused while moving
  (parked or standstill only, same gate as the page) and refused when navigation is
  disabled on the device.
- `cancelRoute()`: allowed any time, the passenger rule.
- `getNavState()`: one live guidance snapshot (route state, upcoming maneuvers with
  distances, lanes, time and distance remaining, audio cue stage) for a polling head
  unit client. Read-only, so it has no gate; `active: false` means navigationd is not
  publishing. The page serves the same payload at `GET /api/state` for clients on the
  car's network.

Refusals come back as JSON-RPC errors carrying the same sentences the page uses.
sunnypilot's own sunnylink connection shares the method table, so the same calls work
over it where it is available.

## Expectations

- Routing needs internet. If the connection drops mid-drive, guidance holds the route
  it already has; it just cannot reroute until the connection returns.
- ETAs reflect traffic at the time the route was requested, not live conditions.
- Leaving the route triggers a reroute. A chosen alternate is kept while Mapbox still
  offers it and falls back to the fastest route when it does not.
- Setting a destination requires the car to be parked or stopped. Canceling is always
  allowed, including by a passenger from the destination page mid-drive.
- To cancel from the wheel: hold the navigation banner for about a second. From the
  page: the Cancel button. From settings: Clear Current Route.

## Credits

- **discountchubbs**: the original navd port and daemon this work is built on.
- The sunnypilot team and contributors, whose UI and speed limit machinery this
  feature plugs into.
