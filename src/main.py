from typing import List, Dict

import numpy as np
import pyperclip
from textual import on, log, events
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll, Horizontal
from textual.reactive import reactive, var
from textual.widget import Widget
from textual.widgets import Footer, Header, Select, Label, RadioSet, RadioButton, \
    LoadingIndicator, Input, Button, Placeholder, SelectionList

from ptarmigan.app_state import CachedAppState, DataPortalEnum, FormatEnum
from ptarmigan.data_state import get_data, clear_cache, get_endpoint_url
from ptarmigan.pandas_table import PandasTable


class ResultTypeSelector(Widget):
    data_portal: reactive[DataPortalEnum] = reactive(None, init=False, recompose=True)
    format: reactive[FormatEnum] = reactive(None, init=False, recompose=True)
    result_type: reactive[str] = reactive(None, init=False, recompose=False)

    DEFAULT_CSS = """
    #result_picker {
        height: 100%;
    }
    Label {
        padding-left: 3
    }
    
    ResultTypeSelector {
        border: solid $accent;
    }
    """

    BORDER_TITLE = "Result type"

    def compose(self) -> ComposeResult:

        with VerticalScroll():
            if not (self.data_portal and self.format):
                yield LoadingIndicator(classes='list_heading')
            else:
                results = get_data(f"results?dataPortal={self.data_portal.value}&format={self.format.value}")

                if self.result_type and not np.any(results.data.resultId.str.contains("study")):
                    # An invalid result type was previously set
                    self.result_type = None

                with RadioSet(id="result_picker"):
                    for idx, result in results.data.iterrows():
                        yield RadioButton(result["resultId"], value=result["resultId"] == self.result_type)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self.result_type = event.pressed.label

    def on_mount(self):
        if not self.result_type:
            self.result_type = "study"
        self.recompose()


class SearchForm(Widget):
    data_portal: reactive[DataPortalEnum] = reactive(None, init=False, recompose=True)
    format: reactive[FormatEnum] = reactive(None, init=False, recompose=True)
    result_type: reactive[str] = reactive(None, init=False, recompose=True)
    queries: reactive[str] = reactive(None, init=False, recompose=False)

    DEFAULT_CSS = """
    SearchForm {
        border: solid $accent;
    }
    """

    BORDER_TITLE = "Queries"

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            if not (self.data_portal and self.format and self.result_type):
                yield LoadingIndicator(classes='list_heading')
            else:
                search_fields = get_data(f"searchFields?dataPortal={self.data_portal.value}&format={self.format.value}&result={self.result_type}")

                for _, field in search_fields.data.iterrows():
                    yield Input(placeholder=field["columnId"], id=field["columnId"])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query_string = ""
        for input_field in self.query(Input).results():
            if input_field.value:
                if not query_string:
                    query_string = f"{input_field.id}={input_field.value}"
                else:
                    query_string += f" AND {input_field.id}={input_field.value}"
        self.queries = query_string


class GlobalOptions(Widget):
    data_portal: reactive[DataPortalEnum] = reactive(None, init=False, recompose=True)
    format: reactive[FormatEnum] = reactive(None, init=False)

    def compose(self) -> ComposeResult:
        with Horizontal():
            if not (self.data_portal and self.format):
                yield LoadingIndicator()
            else:
                yield Select(
                    [(f"{portal.value.title()} data portal", portal) for portal in DataPortalEnum],
                    value=self.data_portal,
                    id="portal-selector"
                )

                yield Select(
                    [(f"{format.name}", format) for format in FormatEnum],
                    value=self.format,
                    id="format-selector"
                )

    @on(Select.Changed)
    def select_changed(self, event: Select.Changed) -> None:
        cached_state = CachedAppState()
        log('EVENTCONTROLID', event.control.id)
        if event.control.id == 'portal-selector':
            log(f"Setting data portal state to {event.value}")
            cached_state.update_state("data_portal", event.value)
        if event.control.id == "format-selector":
            log(f"Setting format state to {event.value}")
            cached_state.update_state('format', event.value)


class ReturnFieldsSelector(Widget):
    data_portal: reactive[DataPortalEnum] = reactive(None, init=False, recompose=True)
    format: reactive[FormatEnum] = reactive(None, init=False, recompose=True)
    result_type: reactive[str] = reactive(None, init=False, recompose=True)
    return_fields: reactive[List[str]] = reactive([], init=True, recompose=False)

    DEFAULT_CSS = """
        ReturnFieldsSelector {
            border: solid $accent;
            height: 100%;
        }
        """

    BORDER_TITLE = "Fields"

    BINDINGS = [
        ("a", "select_all", "Select all")
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            if not (self.data_portal and self.format and self.result_type):
                yield LoadingIndicator()
            else:
                results = get_data(f"returnFields?dataPortal={self.data_portal.value}&format={self.format.value}&result={self.result_type}")

                yield SelectionList[str](
                    *[(result["columnId"], result["columnId"]) for _, result in results.data.iterrows()],
                )

    @on(SelectionList.SelectedChanged)
    def update_selected_view(self) -> None:
        self.return_fields = self.query_one(SelectionList).selected

    def action_select_all(self):
        self.query_one(SelectionList).select_all()


class SearchResults(Widget):
    data_portal: reactive[DataPortalEnum] = reactive(None, init=False, recompose=True)
    format: reactive[FormatEnum] = reactive(None, init=False, recompose=True)
    result_type: reactive[str] = reactive(None, init=False, recompose=True)
    queries: reactive[str] = reactive({}, init=False, recompose=True)
    limit: reactive[int] = reactive(25, init=False, recompose=True)
    return_fields: reactive[List[str]] = reactive([], init=True, recompose=True)

    DEFAULT_CSS = """
    SearchResults {
        border: solid $accent;
    }
    """

    BINDINGS = [
        ("u", "show_url", "Show query URL"),
        ("p", "copy_url", "Copy query URL"),
        ("m", "load_more", "Load more"),
    ]

    def compose(self) -> ComposeResult:
        self.border_title = f"Results for {self.result_type}"
        with VerticalScroll():
            if not (self.data_portal and self.format and self.result_type):
                yield LoadingIndicator(classes='list_heading')
            else:
                qs = f"&query=\"{self.queries}\"" if self.queries else ""

                rfs = f"&fields={','.join(self.return_fields)}" if self.return_fields else ""

                results = get_data(
                    f"search?result={self.result_type}&dataPortal={self.data_portal.value}&format={self.format.value}&limit={self.limit}{qs}{rfs}")
                tab = PandasTable(results.data)
                yield tab

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "more":
            self.action_load_more()

    def action_load_more(self):
        self.limit += 25

    def action_show_url(self):
        qs = f"&query=\"{self.queries}\"" if self.queries else ""
        rfs = f"&fields={','.join(self.return_fields)}" if self.return_fields else ""
        self.notify(get_endpoint_url(f"search?result={self.result_type}&dataPortal={self.data_portal.value}&format={self.format.value}{qs}{rfs}"))

    def action_copy_url(self):
        qs = f"&query=\"{self.queries}\"" if self.queries else ""
        rfs = f"&fields={','.join(self.return_fields)}" if self.return_fields else ""
        pyperclip.copy(get_endpoint_url(f"search?result={self.result_type}&dataPortal={self.data_portal.value}&format={self.format.value}{qs}{rfs}"))
        self.notify("Copied!")


class PortalPtarmigan(App):
    """ENA Portal API Browser app."""

    CSS = """
    .main-app {
        layout: grid;
        grid-size: 4 2;
        grid-columns: 30 30 4fr 30;
        grid-rows: 4 5fr;
    }
    #global-options {
        column-span: 4;
        border-bottom: solid grey;
    }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("c", "clear_cache", "Clear cache")
    ]

    cached_app_state = CachedAppState().state
    data_portal: reactive[DataPortalEnum] = reactive(cached_app_state.data_portal)
    format: reactive[FormatEnum] = reactive(cached_app_state.format)
    result_type: reactive[str] = reactive("study")
    queries: reactive[str] = reactive(None)
    return_fields: reactive[List[str]] = reactive([])

    def compose(self) -> ComposeResult:
        """Compose our UI."""
        # path = "./" if len(sys.argv) < 2 else sys.argv[1]
        yield Header()

        with Container(classes="main-app"):
            yield GlobalOptions(id="global-options")
            # The menu of result types
            rts = ResultTypeSelector()
            yield rts
            self.watch(rts, "result_type", self.update_result_type, init=False)

            sf = SearchForm()
            yield sf
            self.watch(sf, "queries", self.update_queries, init=False)

            yield SearchResults()

            rfs = ReturnFieldsSelector()
            yield rfs
            self.watch(rfs, "return_fields", self.update_return_fields, init=False)

        yield Footer()

    def watch_format(self, format: FormatEnum) -> None:
        self.query_one(ResultTypeSelector).format = format
        self.query_one(GlobalOptions).format = format
        self.query_one(SearchForm).format = format
        self.query_one(SearchResults).format = format
        self.query_one(ReturnFieldsSelector).format = format

    def watch_data_portal(self, data_portal: DataPortalEnum) -> None:
        self.query_one(ResultTypeSelector).data_portal = data_portal
        self.query_one(GlobalOptions).data_portal = data_portal
        self.query_one(SearchForm).data_portal = data_portal
        self.query_one(SearchResults).data_portal = data_portal
        self.query_one(ReturnFieldsSelector).data_portal = data_portal

    def watch_result_type(self, result_type: str) -> None:
        self.query_one(SearchForm).result_type = result_type
        self.query_one(SearchResults).result_type = result_type
        self.return_fields = []
        self.query_one(ResultTypeSelector).result_type = result_type
        self.query_one(ReturnFieldsSelector).result_type = result_type

    def watch_queries(self, queries: dict[str, str]) -> None:
        self.query_one(SearchResults).queries = queries

    def watch_return_fields(self, return_fields: list[str]) -> None:
        self.query_one(SearchResults).return_fields = return_fields

    def update_format(self) -> None:
        cached_app_state = CachedAppState().state
        self.format = cached_app_state.format
        log(f"Will update FORMAT to {self.format}")

    def update_data_portal(self) -> None:
        cached_app_state = CachedAppState().state
        self.data_portal = cached_app_state.data_portal
        log(f"Will update DATAPORTAL to {self.data_portal}")

    def update_return_fields(self, old, new) -> None:
        self.return_fields = new

    def update_result_type(self, old, new) -> None:
        self.result_type = new

    def update_queries(self, old, new) -> None:
        self.queries = new

    def on_mount(self) -> None:
        self.title = "ENA Portal Ptarmigan"
        self.sub_title = "Explore the ENA Portal API"
        self.update_format()
        self.update_data_portal()

    def action_clear_cache(self) -> None:
        clear_cache()
        self.update_format()


def main():
    PortalPtarmigan().run()


if __name__ == "__main__":
    main()