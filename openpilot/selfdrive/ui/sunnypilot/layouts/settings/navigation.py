"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
from functools import partial

from openpilot.common.params import Params
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import DialogResult, Widget
from openpilot.system.ui.widgets.confirm_dialog import alert_dialog
from openpilot.system.ui.widgets.list_view import button_item
from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
from openpilot.system.ui.widgets.scroller_tici import Scroller

from openpilot.system.ui.sunnypilot.widgets.input_dialog import InputDialogSP
from openpilot.system.ui.sunnypilot.widgets.list_view import toggle_item_sp, multiple_button_item_sp


class NavigationLayout(Widget):
  def __init__(self):
    super().__init__()

    self._params = Params()
    self._dialog: MultiOptionDialog | None = None

    self._mapbox_token_item = button_item(tr("Mapbox token"), tr("Edit"), tr("Enter your Mapbox public token"),
                                          partial(self._show_param_input, "MapboxToken", tr("Enter Mapbox Token")))
    self._mapbox_route_item = button_item(tr("Mapbox route"), tr("Edit"), "",
                                          partial(self._show_param_input, "MapboxRoute", tr("Enter Mapbox Route")))

    # only shown while navigation is enabled
    self._vis_items = [
      button_item(tr("Set Home"), tr("Set"), "", partial(self._open_fav_dialog, "home", tr("Set Home Route"))),
      button_item(tr("Set Work"), tr("Set"), "", partial(self._open_fav_dialog, "work", tr("Set Work Route"))),
      button_item(tr("Add Favorite"), tr("Add"), tr("Add a new favorite"), self._add_fav),
      button_item(tr("Remove Favorite"), tr("Remove"), tr("Remove a favorite"), self._remove_fav),
      toggle_item_sp(tr("Mapbox recompute"), tr("Enable automatic route recomputation"), param="MapboxRecompute"),
      toggle_item_sp(tr("Navigation desires"), tr("Allow navigation to automatically take turns"), param="NavDesiresAllowed"),
      toggle_item_sp(tr("Navigation banners"), tr("Show turn-by-turn instructions as onroad alerts"), param="NavEvents"),
    ]

    items = [
      self._mapbox_token_item, self._mapbox_route_item,
      button_item(tr("Clear current route"), tr("Clear"), "", self._clear_route),
      multiple_button_item_sp(tr("Favorites"), tr("Select favorite route"), [tr("Home"), tr("Work"), tr("Favorites")], 0,
                              callback=self._favorites_callback),
      *self._vis_items[:4],
      toggle_item_sp(tr("Allow navigation"), tr("Enable navigation service"), callback=self._update_navigation_visibility,
                     param="AllowNavigation"),
      *self._vis_items[4:],
    ]
    self._scroller = Scroller(items, line_separator=True, spacing=0)

  @property
  def _favs(self) -> dict:
    try:
      return json.loads(self._params.get("MapboxFavorites") or "{}")
    except json.JSONDecodeError:
      return {}

  def _show_param_input(self, param: str, title: str) -> None:
    InputDialogSP(title, current_text=self._params.get(param) or "", param=param).show()

  def _clear_route(self) -> None:
    # navigationd drops the active route once the destination param is empty
    self._params.put("MapboxRoute", "")

  def _handle_save_fav(self, key: str, is_fav: bool, res: DialogResult, text: str) -> None:
    if res == DialogResult.CONFIRM and text:
      favs = self._favs
      (favs.setdefault("favorites", {}) if is_fav else favs)[key] = text
      self._params.put("MapboxFavorites", json.dumps(favs))

  def _open_fav_dialog(self, key: str, title: str) -> None:
    InputDialogSP(title, current_text=self._favs.get(key, ""), callback=partial(self._handle_save_fav, key, False)).show()

  def _add_fav_name_cb(self, res: DialogResult, name: str) -> None:
    if res == DialogResult.CONFIRM and name:
      InputDialogSP(tr("Set Route for %s") % name, callback=partial(self._handle_save_fav, name, True), min_text_size=1).show()

  def _add_fav(self) -> None:
    InputDialogSP(tr("Favorite Name"), callback=self._add_fav_name_cb, min_text_size=1).show()

  def _set_mapbox_route_cb(self, favorites: dict, selection: str) -> None:
    self._params.put("MapboxRoute", favorites[selection])

  def _favorites_callback(self, index: int) -> None:
    favs = self._favs
    if index < 2:
      if route := favs.get(["home", "work"][index]):
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
      self._params.put("MapboxFavorites", json.dumps(favs))

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

  def _update_state(self):
    self._mapbox_token_item.action_item.set_value(self._params.get("MapboxToken") or tr("Mapbox token not set"))
    self._mapbox_route_item.action_item.set_value(self._params.get("MapboxRoute") or tr("Destination not set"))
    self._update_navigation_visibility(self._params.get_bool("AllowNavigation"))

  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    self._scroller.show_event()
