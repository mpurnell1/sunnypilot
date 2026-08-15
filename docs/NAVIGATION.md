# Navigation

sunnypilot navigation is a driving aid: it shows and speaks turn-by-turn guidance while
you drive the car. It is not point-to-point autonomy. The route never steers the car,
changes lanes, or takes an exit on its own; every maneuver is yours to make, with the
same attention driving always demands.

This implementation builds on the navd work of **discountchubbs**, whose navigation
daemon is the foundation everything here extends. If you find this feature useful,
that groundwork is why it exists.

## What it does

- A quiet chip in the onroad UI carries the route state: absent means no route, dimmed
  means searching, a glyph with a distance means routing. It expands into a banner as a
  maneuver approaches and gets out of the way after.
- Audio cues, if enabled, sound as maneuvers approach. The sound tells you what and
  when; the screen tells you where.
- The arrival pill shows remaining time, distance, and arrival time.
- While navigating, the route's speed limit can fill in the speed limit sign when
  neither the car nor map data knows one. Route data can be stale; posted signs win.
- Optional, off by default, and always driver-confirmed: navigation can suggest turns
  and lane changes to the driving model, but only after your own blinker or steering
  input agrees. Nothing acts without a driver signal.

## Setup

1. Set a Mapbox token in Settings, then enable **Allow Navigation** under
   Settings, Navigation.
2. Set a destination:
   - From a phone on the same network or the device hotspot, open
     `http://<device-ip>:5050`. Search, compare routes with live traffic, and go.
   - From anywhere, comma prime users can send a destination through athena
     (`tools/send_nav_destination.py`).
   - From the device, pick a saved favorite in Settings, Navigation.
3. Route choice is yours: the destination page previews alternates with live and
   typical times. A favorite can be bound to a route you always want; a bound favorite
   starts navigating on that route in one tap.

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
