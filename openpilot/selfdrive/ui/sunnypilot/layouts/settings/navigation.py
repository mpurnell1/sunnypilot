"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl
from functools import partial

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.sunnypilot.nav_status import NavState, NavStatus
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import DialogResult, Widget
from openpilot.system.ui.widgets.confirm_dialog import alert_dialog
from openpilot.system.ui.widgets.list_view import button_item
from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
from openpilot.system.ui.widgets.scroller_tici import Scroller

from openpilot.system.ui.sunnypilot.widgets import get_highlighted_description
from openpilot.system.ui.sunnypilot.widgets.input_dialog import InputDialogSP
from openpilot.system.ui.sunnypilot.widgets.list_view import toggle_item_sp, multiple_button_item_sp

NAV_BANNER_BUTTONS = [tr("Off"), tr("Increments"), tr("Always")]

NAV_BANNER_DESCRIPTIONS = [
  tr("Off: Show no turn-by-turn instructions."),
  tr("Increments: Show the next instruction as you approach each turn."),
  tr("Always: Keep the next instruction on screen for the whole route."),
]

# NavHudMode bitmask: 1 = turn card, 2 = route summary pill
NAV_HUD_BUTTONS = [tr("Off"), tr("Turns"), tr("ETA"), tr("Both")]

NAV_HUD_DESCRIPTIONS = [
  tr("Off: No navigation HUD elements."),
  tr("Turns: Next-turn card under the set speed."),
  tr("ETA: Arrival pill in the bottom-right corner."),
  tr("Both: Turn card and arrival pill."),
]

NAV_LANE_BUTTONS = [tr("Off"), tr("Display"), tr("Assist")]

NAV_LANE_DESCRIPTIONS = [
  tr("Off: No lane guidance."),
  tr("Display: Show which lanes lead to the next maneuver on the turn card."),
  tr("Assist: Also confirm a lane change you signal toward the route immediately, without a steering nudge or delay. You still start every lane change with the blinker."),  # noqa: E501
]

NAV_AUDIO_BUTTONS = [tr("Off"), tr("Tones"), tr("Morse")]

NAV_AUDIO_DESCRIPTIONS = [
  tr("Off: No navigation sounds."),
  tr("Tones: Short pitch cues for each maneuver; rising means right, falling means left, wider means sharper."),
  tr("Morse: Maneuver codes keyed in Morse, e.g. R for a right turn or O3 for a roundabout's third exit. Speed comes from the NavAudioWpm parameter."),
]

STATUS_GOOD_COLOR = rl.Color(0x2f, 0xc4, 0x6e, 0xff)
STATUS_PENDING_COLOR = rl.Color(0xff, 0xb8, 0x2e, 0xff)
STATUS_FAILED_COLOR = rl.Color(0xf2, 0x6d, 0x6d, 0xff)
STATUS_IDLE_COLOR = rl.Color(170, 170, 170, 255)


class NavigationLayout(Widget):
  def __init__(self):
    super().__init__()

    self._params = Params()
    self._dialog: MultiOptionDialog | None = None
    self._nav_status = NavStatus()

    # The two preconditions navigation waits on. Each row both reports its live state and
    # switches the matching onroad icon, so the reading and the control sit together.
    self._gps_status_item = toggle_item_sp(tr("GPS Indicator"),
                                           tr("Display a location pin under the set speed, green once the GPS fix is good."),
                                           param="NavShowGpsIcon")
    self._gps_status_item.set_right_value(lambda: self._nav_status.gps_text)
    self._route_status_item = toggle_item_sp(tr("Route Indicator"),
                                             tr("Display a destination flag under the set speed, green once a route is loaded."),
                                             param="NavShowRouteIcon")
    self._route_status_item.set_right_value(lambda: self._nav_status.route_text)
    self._nav_hud_item = multiple_button_item_sp(tr("Navigation HUD"), self._get_nav_hud_description,
                                                 NAV_HUD_BUTTONS, param="NavHudMode")
    self._lane_guidance_item = multiple_button_item_sp(tr("Lane Guidance"), self._get_lane_guidance_description,
                                                       NAV_LANE_BUTTONS, param="NavLaneGuidance")
    self._nav_audio_item = multiple_button_item_sp(tr("Navigation Audio"), self._get_nav_audio_description,
                                                   NAV_AUDIO_BUTTONS, param="NavigationAudio")
    self._sound_tour_item = button_item(tr("Sound Tour"), tr("Play"),
                                        tr("Play every navigation sound in your selected style, in this order: right turn approaching then imminent, left turn, slight and sharp turns, keep, exit, merge, U-turn, roundabout with an exit-count tick per exit, lane changes, a turn with mile ticks, rerouting, and arrival."),  # noqa: E501
                                        self._play_sound_tour)

    self._mapbox_token_item = button_item(tr("Mapbox Token"), tr("Edit"), tr("Enter your Mapbox public token."),
                                          partial(self._show_param_input, "MapboxToken", tr("Enter Mapbox Token")))
    self._mapbox_route_item = button_item(tr("Mapbox Route"), tr("Edit"), "",
                                          partial(self._show_param_input, "MapboxRoute", tr("Enter Mapbox Route")))

    # only shown while navigation is enabled
    self._vis_items = [
      button_item(tr("Set Home"), tr("Set"), "", partial(self._open_fav_dialog, "home", tr("Set Home Route"))),
      button_item(tr("Set Work"), tr("Set"), "", partial(self._open_fav_dialog, "work", tr("Set Work Route"))),
      button_item(tr("Add Favorite"), tr("Add"), tr("Add a new favorite."), self._add_fav),
      button_item(tr("Remove Favorite"), tr("Remove"), tr("Remove a favorite."), self._remove_fav),
      toggle_item_sp(tr("Mapbox Recompute"), tr("Recompute the route automatically after leaving it."), param="MapboxRecompute"),
      toggle_item_sp(tr("Navigation Desires"), tr("Steer through a turn on the route once you signal for it."), param="NavDesiresAllowed"),
      multiple_button_item_sp(tr("Navigation Banners"), self._get_banner_description,
                              NAV_BANNER_BUTTONS, param="NavBannerMode"),
    ]

    # -1: nothing highlighted until the destination actually matches a saved place
    self._favorites_item = multiple_button_item_sp(tr("Favorites"), tr("Select a saved destination."), [tr("Home"), tr("Work"), tr("Favorites")], -1,
                                                   callback=self._favorites_callback)

    items = [
      self._mapbox_token_item, self._mapbox_route_item,
      button_item(tr("Clear Current Route"), tr("Clear"), "", self._clear_route),
      self._favorites_item,
      *self._vis_items[:4],
      toggle_item_sp(tr("Allow Navigation"), tr("Enable the navigation service."), callback=self._update_navigation_visibility,
                     param="AllowNavigation"),
      *self._vis_items[4:],
      self._gps_status_item, self._route_status_item,
      self._nav_hud_item, self._lane_guidance_item, self._nav_audio_item, self._sound_tour_item,
    ]
    self._scroller = Scroller(items, line_separator=True, spacing=0)

  @property
  def _favs(self) -> dict:
    # MapboxFavorites is a JSON-typed param, so Params returns it already deserialized
    return self._params.get("MapboxFavorites") or {}

  def _show_param_input(self, param: str, title: str) -> None:
    InputDialogSP(title, current_text=self._params.get(param) or "", param=param).show()

  def _clear_route(self) -> None:
    # navigationd drops the active route once the destination param is empty; the log line
    # attributes the clear to a deliberate tap, distinguishing it from a param glitch
    cloudlog.warning("ui: destination cleared from navigation settings")
    self._params.put("MapboxRoute", "")

  def _handle_save_fav(self, key: str, is_fav: bool, res: DialogResult, text: str) -> None:
    if res == DialogResult.CONFIRM and text:
      favs = self._favs
      (favs.setdefault("favorites", {}) if is_fav else favs)[key] = text
      self._params.put("MapboxFavorites", favs)

  def _open_fav_dialog(self, key: str, title: str) -> None:
    InputDialogSP(title, current_text=self._favs.get(key, ""), callback=partial(self._handle_save_fav, key, False)).show()

  def _add_fav_name_cb(self, res: DialogResult, name: str) -> None:
    if res == DialogResult.CONFIRM and name:
      InputDialogSP(tr("Set Route for %s") % name, callback=partial(self._handle_save_fav, name, True), min_text_size=1).show()

  def _add_fav(self) -> None:
    InputDialogSP(tr("Favorite Name"), callback=self._add_fav_name_cb, min_text_size=1).show()

  def _set_mapbox_route_cb(self, favorites: dict, selection: str) -> None:
    cloudlog.warning("ui: destination set from favorite %r", selection)
    self._params.put("MapboxRoute", favorites[selection])

  def _favorites_callback(self, index: int) -> None:
    favs = self._favs
    if index < 2:
      if route := favs.get(["home", "work"][index]):
        cloudlog.warning("ui: destination set from favorite %r", ["home", "work"][index])
        self._params.put("MapboxRoute", route)
      else:
        gui_app.push_widget(alert_dialog(tr("No route set")))
    elif favorites := favs.get("favorites"):
      self._show_list_dialog(tr("Select Favorite"), list(favorites.keys()), partial(self._set_mapbox_route_cb, favorites))
    else:
      gui_app.push_widget(alert_dialog(tr("No custom favorites set")))

  def _remove_fav_cb(self, selection: str) -> None:
    favs = self._favs
    if favs.get("favorites", {}).pop(selection, None):
      self._params.put("MapboxFavorites", favs)

  def _remove_fav(self) -> None:
    if favorites := self._favs.get("favorites"):
      self._show_list_dialog(tr("Remove Favorite"), list(favorites.keys()), self._remove_fav_cb)
    else:
      gui_app.push_widget(alert_dialog(tr("No custom favorites to remove")))

  def _show_list_dialog(self, title: str, items: list[str], callback) -> None:
    def handle_selection(result: DialogResult) -> None:
      if result == DialogResult.CONFIRM and self._dialog is not None and self._dialog.selection:
        callback(self._dialog.selection)
      self._dialog = None

    self._dialog = MultiOptionDialog(title, items, callback=handle_selection)
    gui_app.push_widget(self._dialog)

  def _update_navigation_visibility(self, state: bool) -> None:
    for item in self._vis_items:
      item.set_visible(state)

  def _get_banner_description(self) -> str:
    return get_highlighted_description(self._params, "NavBannerMode", NAV_BANNER_DESCRIPTIONS)

  def _get_nav_hud_description(self) -> str:
    return get_highlighted_description(self._params, "NavHudMode", NAV_HUD_DESCRIPTIONS)

  def _get_lane_guidance_description(self) -> str:
    return get_highlighted_description(self._params, "NavLaneGuidance", NAV_LANE_DESCRIPTIONS)

  def _play_sound_tour(self) -> None:
    # soundd polls this at 2 Hz and plays the tour through the normal nav audio channel
    self._params.put_bool("NavAudioTourRequest", True)

  def _get_nav_audio_description(self) -> str:
    return get_highlighted_description(self._params, "NavigationAudio", NAV_AUDIO_DESCRIPTIONS)

  def _update_state(self):
    self._nav_status.update()
    # the toggle owns the right item, so the live state is coloured through the value text
    self._gps_status_item._right_value_color = STATUS_GOOD_COLOR if self._nav_status.gps_locked else (
      STATUS_IDLE_COLOR if self._nav_status.state == NavState.OFFLINE else STATUS_PENDING_COLOR)
    self._route_status_item._right_value_color = {
      NavState.OFFLINE: STATUS_IDLE_COLOR,
      NavState.NO_DESTINATION: STATUS_IDLE_COLOR,
      NavState.WAITING_FOR_GPS: STATUS_PENDING_COLOR,
      NavState.COMPUTING: STATUS_PENDING_COLOR,
      NavState.NO_ROUTE: STATUS_FAILED_COLOR,
      NavState.ACTIVE: STATUS_GOOD_COLOR,
    }[self._nav_status.state]

    self._mapbox_token_item.action_item.set_value(self._params.get("MapboxToken") or tr("Mapbox token not set"))
    self._mapbox_route_item.action_item.set_value(self._params.get("MapboxRoute") or tr("Destination not set"))
    self._update_navigation_visibility(self._params.get_bool("AllowNavigation"))

    # the highlight mirrors the actual destination rather than the last tap, so a tap that
    # saved nothing, or a destination set/cleared elsewhere, can't leave it stale
    route = self._params.get("MapboxRoute")
    favs = self._favs
    if route and route == favs.get("home"):
      selected = 0
    elif route and route == favs.get("work"):
      selected = 1
    elif route and route in (favs.get("favorites") or {}).values():
      selected = 2
    else:
      selected = -1
    self._favorites_item.action_item.selected_button = selected

  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    self._scroller.show_event()
