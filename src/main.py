import time
from typing import List, Dict

import numpy as np
import pyperclip
from textual import on, log, events
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll, Horizontal
from textual.message import Message
from textual.reactive import reactive, var
from textual.widget import Widget
from textual.widgets import Footer, Header, Select, Label, RadioSet, RadioButton, \
    LoadingIndicator, Input, Button, Placeholder, SelectionList, Static, OptionList, DataTable

from ptarmigan.app_state import CachedAppState, DataPortalEnum, FormatEnum
from ptarmigan.data_state import get_data, clear_cache, get_endpoint_url
from ptarmigan.pandas_table import PandasTable


class ListFilter(Widget):
    DEFAULT_CSS = """
    ListFilter {
        height: auto;
        border-bottom: solid $accent;
        padding: 0 1;
    }

    ListFilter.hidden {
        display: none;
    }

    ListFilter Input {
        margin-bottom: 1;
    }

    ListFilter OptionList {
        height: 6;
        width: 72;
        max-width: 80vw;
        overlay: screen;
        layer: overlay;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    class Selected(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    choices: reactive[List[str]] = reactive([], init=False)

    def __init__(self, choices: List[str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.choices = choices or []
        self.matches: List[str] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter...")
        yield OptionList()

    def on_mount(self) -> None:
        self.refresh_matches()

    def open(self) -> None:
        self.remove_class("hidden")
        filter_input = self.query_one(Input)
        filter_input.value = ""
        filter_input.focus()
        self.refresh_matches()

    def close(self) -> None:
        self.add_class("hidden")

    def refresh_matches(self, needle: str = "") -> None:
        needle = needle.casefold()
        if needle:
            self.matches = [choice for choice in self.choices if needle in choice.casefold()]
        else:
            self.matches = self.choices[:]

        option_list = self.query_one(OptionList)
        option_list.clear_options()
        option_list.add_options(self.matches[:25] or ["No matches"])
        option_list.highlighted = 0 if self.matches else None

    def select_highlighted(self) -> None:
        option_list = self.query_one(OptionList)
        highlighted = option_list.highlighted
        if highlighted is not None and highlighted < len(self.matches):
            self.post_message(self.Selected(self.matches[highlighted]))

    @on(Input.Changed)
    def filter_changed(self, event: Input.Changed) -> None:
        self.refresh_matches(event.value)

    @on(Input.Submitted)
    def filter_submitted(self) -> None:
        self.select_highlighted()

    @on(OptionList.OptionSelected)
    def option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.index < len(self.matches):
            self.post_message(self.Selected(self.matches[event.index]))

    def on_key(self, event: events.Key) -> None:
        option_list = self.query_one(OptionList)
        if event.key == "escape":
            self.close()
            event.stop()
            event.prevent_default()
        elif event.key == "down":
            option_list.action_cursor_down()
            event.stop()
            event.prevent_default()
        elif event.key == "up":
            option_list.action_cursor_up()
            event.stop()
            event.prevent_default()
        elif event.key == "enter":
            self.select_highlighted()
            event.stop()
            event.prevent_default()


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

                if results.error:
                    yield Static(results.error, classes="error")
                    return

                if self.result_type and not np.any(results.data.resultId.str.contains("study")):
                    # An invalid result type was previously set
                    self.result_type = None

                with RadioSet(id="result_picker"):
                    for idx, result in results.data.iterrows():
                        yield RadioButton(result["resultId"], value=result["resultId"] == self.result_type)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self.result_type = event.pressed.label

    async def on_mount(self):
        if not self.result_type:
            self.result_type = "study"
        await self.recompose()


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

    BINDINGS = [
        ("/", "open_filter", "Find query field")
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            if not (self.data_portal and self.format and self.result_type):
                yield LoadingIndicator(classes='list_heading')
            else:
                search_fields = get_data(f"searchFields?dataPortal={self.data_portal.value}&format={self.format.value}&result={self.result_type}")

                if search_fields.error:
                    yield Static(search_fields.error, classes="error")
                    return

                field_ids = search_fields.data.columnId.tolist()
                yield ListFilter(field_ids, id="query-filter", classes="hidden")
                for _, field in search_fields.data.iterrows():
                    yield Input(placeholder=field["columnId"], id=field["columnId"])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if isinstance(event.input.parent, ListFilter):
            return

        query_string = ""
        for input_field in self.query(Input).results():
            if isinstance(input_field.parent, ListFilter):
                continue
            if input_field.value:
                if not query_string:
                    query_string = f"{input_field.id}={input_field.value}"
                else:
                    query_string += f" AND {input_field.id}={input_field.value}"
        self.queries = query_string

    def on_key(self, event: events.Key) -> None:
        if event.key != "tab":
            return

        focused = self.app.focused
        if isinstance(focused, Input) and not isinstance(focused.parent, ListFilter):
            results_table = self.app.query_one(SearchResults).query_one_optional(DataTable)
            if results_table is not None:
                results_table.focus()
                event.stop()
                event.prevent_default()

    def action_open_filter(self) -> None:
        self.query_one("#query-filter", ListFilter).open()

    def on_list_filter_selected(self, event: ListFilter.Selected) -> None:
        event.stop()
        self.query_one("#query-filter", ListFilter).close()
        for input_field in self.query(Input).results():
            if input_field.id == event.value:
                input_field.focus()
                input_field.scroll_visible(immediate=True)
                return


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
            self.app.data_portal = event.value
        if event.control.id == "format-selector":
            log(f"Setting format state to {event.value}")
            cached_state.update_state('format', event.value)
            self.app.format = event.value


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
        ("a", "select_all", "Select all"),
        ("/", "open_filter", "Find return field")
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            if not (self.data_portal and self.format and self.result_type):
                yield LoadingIndicator()
            else:
                results = get_data(f"returnFields?dataPortal={self.data_portal.value}&format={self.format.value}&result={self.result_type}")

                if results.error:
                    yield Static(results.error, classes="error")
                    return

                field_ids = results.data.columnId.tolist()
                yield ListFilter(field_ids, id="return-fields-filter", classes="hidden")
                yield SelectionList[str](
                    *[(result["columnId"], result["columnId"]) for _, result in results.data.iterrows()],
                )

    @on(SelectionList.SelectedChanged)
    def update_selected_view(self) -> None:
        self.return_fields = self.query_one(SelectionList).selected

    def action_select_all(self):
        self.query_one(SelectionList).select_all()

    def action_open_filter(self) -> None:
        self.query_one("#return-fields-filter", ListFilter).open()

    def on_list_filter_selected(self, event: ListFilter.Selected) -> None:
        event.stop()
        self.query_one("#return-fields-filter", ListFilter).close()
        selection_list = self.query_one(SelectionList)
        for index, option in enumerate(selection_list.options):
            if option.prompt == event.value:
                selection_list.highlighted = index
                selection_list.focus()
                selection_list.scroll_to_highlight()
                return


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
        ("d", "dump_data", "Dump to TSV")
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
                if results.error:
                    yield Static(results.error, classes="error")
                    return

                tab = PandasTable(results.data)
                yield tab

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "more":
            self.action_load_more()

    def action_load_more(self):
        self.limit += 25

    def get_endpoint_without_limit(self):
        qs = f"&query=\"{self.queries}\"" if self.queries else ""
        rfs = f"&fields={','.join(self.return_fields)}" if self.return_fields else ""
        return f"search?result={self.result_type}&dataPortal={self.data_portal.value}&format={self.format.value}{qs}{rfs}"

    def get_url_without_limit(self):
        return get_endpoint_url(self.get_endpoint_without_limit())

    def action_show_url(self):
        self.notify(self.get_url_without_limit())

    def action_copy_url(self):
        pyperclip.copy(self.get_url_without_limit())
        self.notify("Copied!")

    def action_dump_data(self):
        all_data = get_data(self.get_endpoint_without_limit(), use_cache=False)
        if all_data.error:
            self.notify(all_data.error, severity="error")
            return

        fn = f"{self.result_type}-{time.time()}.tsv"
        all_data.data.to_csv(fn, sep='\t', index=False)
        self.notify(f"Dumped to {fn}")


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
