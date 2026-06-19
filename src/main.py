import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict

import numpy as np
import pyperclip
from textual import on, log, events
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll, Horizontal
from textual.message import Message
from textual.reactive import reactive, var
from textual.suggester import SuggestFromList
from textual.widget import Widget
from textual.widgets import Footer, Header, Select, Label, RadioSet, RadioButton, \
    LoadingIndicator, Input, Button, Placeholder, SelectionList, Static, OptionList, DataTable, Checkbox

from ptarmigan.app_state import CachedAppState, DataPortalEnum, FormatEnum
from ptarmigan.data_state import get_data, clear_cache, get_endpoint_url
from ptarmigan.pandas_table import PandasTable


class ENAQueryOperator(str, Enum):
    OR = "OR"
    AND = "AND"


@dataclass(frozen=True)
class ENAQueryClause:
    search_field: str
    value: str
    is_not: bool = False

    def __str__(self) -> str:
        not_prefix = "NOT " if self.is_not else ""
        return f"{not_prefix}{self.search_field}={self.value}"

    def __or__(self, other: "ENAQueryExpression") -> "ENAQueryPair":
        return ENAQueryPair(left=self, operator=ENAQueryOperator.OR, right=other)

    def __and__(self, other: "ENAQueryExpression") -> "ENAQueryPair":
        return ENAQueryPair(left=self, operator=ENAQueryOperator.AND, right=other)

    def __invert__(self) -> "ENAQueryClause":
        return ENAQueryClause(self.search_field, self.value, not self.is_not)


@dataclass(frozen=True)
class ENAQueryPair:
    left: "ENAQueryExpression"
    operator: ENAQueryOperator
    right: "ENAQueryExpression"
    is_not: bool = False

    def __str__(self) -> str:
        not_prefix = "NOT " if self.is_not else ""
        return f"{not_prefix}({self.left} {self.operator.value} {self.right})"

    def __or__(self, other: "ENAQueryExpression") -> "ENAQueryPair":
        return ENAQueryPair(left=self, operator=ENAQueryOperator.OR, right=other)

    def __and__(self, other: "ENAQueryExpression") -> "ENAQueryPair":
        return ENAQueryPair(left=self, operator=ENAQueryOperator.AND, right=other)

    def __invert__(self) -> "ENAQueryPair":
        return ENAQueryPair(self.left, self.operator, self.right, not self.is_not)


ENAQueryExpression = ENAQueryClause | ENAQueryPair


def combine_query_expressions(
        expressions: list[ENAQueryExpression],
        operators: list[ENAQueryOperator],
) -> ENAQueryExpression:
    expression = expressions[0]
    for operator, next_expression in zip(operators, expressions[1:]):
        expression = ENAQueryPair(left=expression, operator=operator, right=next_expression)
    return expression


def has_ancestor(widget: Widget, widget_type: type[Widget]) -> bool:
    parent = widget.parent
    while parent is not None:
        if isinstance(parent, widget_type):
            return True
        parent = parent.parent
    return False


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

    def __init__(self, choices: List[str] | None = None, max_matches: int | None = 25, **kwargs) -> None:
        super().__init__(**kwargs)
        self.choices = choices or []
        self.max_matches = max_matches
        self.matches: List[str] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter...")
        yield OptionList()

    def on_mount(self) -> None:
        self.refresh_matches()

    def open(self, initial_value: str = "") -> None:
        self.remove_class("hidden")
        filter_input = self.query_one(Input)
        filter_input.value = initial_value
        filter_input.focus()
        self.refresh_matches(initial_value)

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
        visible_matches = self.matches if self.max_matches is None else self.matches[:self.max_matches]
        option_list.add_options(visible_matches or ["No matches"])
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


class CompoundQueryBuilder(VerticalScroll):
    DEFAULT_CSS = """
    CompoundQueryBuilder {
        width: 112;
        max-width: 92vw;
        height: 88vh;
        overlay: screen;
        layer: overlay;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
        overflow-x: hidden;
    }

    CompoundQueryBuilder.hidden {
        display: none;
    }

    CompoundQueryBuilder .compound-heading {
        text-style: bold;
        margin-bottom: 1;
    }

    CompoundQueryBuilder .compound-group {
        height: auto;
        border-top: solid $accent;
        padding-top: 1;
        margin-top: 1;
    }

    CompoundQueryBuilder .compound-row {
        height: auto;
        margin-bottom: 1;
    }

    CompoundQueryBuilder .top-operator {
        width: 7;
        text-wrap: nowrap;
    }

    CompoundQueryBuilder .clause-operator {
        width: 7;
        text-wrap: nowrap;
    }

    CompoundQueryBuilder .compound-field {
        width: 34;
    }

    CompoundQueryBuilder .compound-value {
        width: 34;
    }

    CompoundQueryBuilder .compound-not {
        width: 14;
        text-wrap: nowrap;
    }

    CompoundQueryBuilder .compound-preview {
        min-height: 3;
        max-height: 5;
        border: solid $secondary;
        padding: 0 1;
        text-wrap: wrap;
    }

    CompoundQueryBuilder .compound-actions {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }
    """

    GROUP_COUNT = 3
    CLAUSES_PER_GROUP = 3

    class Applied(Message):
        def __init__(self, query: str) -> None:
            self.query = query
            super().__init__()

    def __init__(self, field_ids: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.field_ids = field_ids

    def compose(self) -> ComposeResult:
        yield Static("Compound query builder", classes="compound-heading")
        yield Static(
            "Fill any rows you need. Clauses within a group are parenthesized, then groups are combined.",
            classes="compound-help",
        )
        with Horizontal(classes="compound-actions"):
            yield Button("Apply", id="apply-compound", variant="primary")
            yield Button("Preview", id="preview-compound")
            yield Button("Clear", id="clear-compound")
            yield Button("Close", id="close-compound")
        yield Static("", id="compound-preview", classes="compound-preview")
        for group_index in range(self.GROUP_COUNT):
            with Container(classes="compound-group"):
                with Horizontal(classes="compound-row"):
                    if group_index > 0:
                        yield Button(
                            ENAQueryOperator.AND.value,
                            id=f"compound-group-op-{group_index}",
                            classes="top-operator operator-toggle",
                        )
                    else:
                        yield Static("", classes="top-operator")
                    yield Static(f"Group {group_index + 1}", classes="compound-heading")
                for clause_index in range(self.CLAUSES_PER_GROUP):
                    with Horizontal(classes="compound-row"):
                        if clause_index > 0:
                            yield Button(
                                ENAQueryOperator.AND.value,
                                id=f"compound-clause-op-{group_index}-{clause_index}",
                                classes="clause-operator operator-toggle",
                            )
                        else:
                            yield Static("", classes="clause-operator")
                        yield Input(
                            placeholder="field",
                            suggester=SuggestFromList(self.field_ids, case_sensitive=False),
                            id=f"compound-field-{group_index}-{clause_index}",
                            classes="compound-field",
                        )
                        yield Input(
                            placeholder="value",
                            id=f"compound-value-{group_index}-{clause_index}",
                            classes="compound-value",
                        )
                        yield Checkbox(
                            "NOT",
                            id=f"compound-not-{group_index}-{clause_index}",
                            classes="compound-not",
                        )

    def open(self) -> None:
        self.remove_class("hidden")
        self.scroll_home(animate=False)
        self.query_one(Input).focus()
        self.update_preview()

    def open_with_clause(self, field: str | None = None, value: str | None = None) -> None:
        self.open()
        if field:
            field_input = self.query_one("#compound-field-0-0", Input)
            value_input = self.query_one("#compound-value-0-0", Input)
            field_input.value = field
            if value:
                value_input.value = value
                value_input.focus()
            else:
                field_input.focus()
        self.update_preview()

    def close(self) -> None:
        self.add_class("hidden")

    def build_query(self) -> str:
        group_expressions: list[ENAQueryExpression] = []
        group_operators: list[ENAQueryOperator] = []

        for group_index in range(self.GROUP_COUNT):
            clauses: list[ENAQueryExpression] = []
            clause_operators: list[ENAQueryOperator] = []

            for clause_index in range(self.CLAUSES_PER_GROUP):
                field = self.query_one(f"#compound-field-{group_index}-{clause_index}", Input).value.strip()
                value = self.query_one(f"#compound-value-{group_index}-{clause_index}", Input).value.strip()
                if not field and not value:
                    continue
                if not field or not value:
                    raise ValueError("Each compound clause needs both a field and a value.")

                is_not = self.query_one(f"#compound-not-{group_index}-{clause_index}", Checkbox).value
                if clauses:
                    operator = self.query_one(
                        f"#compound-clause-op-{group_index}-{clause_index}", Button
                    ).label
                    clause_operators.append(ENAQueryOperator(str(operator)))
                clauses.append(ENAQueryClause(field, value, is_not=is_not))

            if not clauses:
                continue

            if group_expressions and group_index > 0:
                operator = self.query_one(f"#compound-group-op-{group_index}", Button).label
                group_operators.append(ENAQueryOperator(str(operator)))
            group_expressions.append(combine_query_expressions(clauses, clause_operators))

        if not group_expressions:
            return ""

        return str(combine_query_expressions(group_expressions, group_operators))

    def update_preview(self) -> None:
        preview = self.query_one("#compound-preview", Static)
        try:
            query = self.build_query()
        except ValueError as error:
            preview.update(str(error))
            return
        preview.update(query or "No compound clauses yet.")

    def clear(self) -> None:
        for input_field in self.query(Input).results():
            input_field.value = ""
        for checkbox in self.query(Checkbox).results():
            checkbox.value = False
        for button in self.query(Button).results():
            if "operator-toggle" in button.classes:
                button.label = ENAQueryOperator.AND.value
        self.update_preview()

    @on(Input.Changed)
    @on(Checkbox.Changed)
    def input_changed(self) -> None:
        self.update_preview()

    @on(Button.Pressed, ".operator-toggle")
    def toggle_operator(self, event: Button.Pressed) -> None:
        event.button.label = (
            ENAQueryOperator.OR.value
            if str(event.button.label) == ENAQueryOperator.AND.value
            else ENAQueryOperator.AND.value
        )
        self.update_preview()
        event.stop()

    @on(Button.Pressed, "#apply-compound")
    def apply_compound(self) -> None:
        try:
            query = self.build_query()
        except ValueError as error:
            self.notify(str(error), severity="error")
            self.update_preview()
            return
        self.post_message(self.Applied(query))
        self.close()

    @on(Button.Pressed, "#preview-compound")
    def preview_compound(self) -> None:
        self.update_preview()

    @on(Button.Pressed, "#clear-compound")
    def clear_compound(self) -> None:
        self.clear()

    @on(Button.Pressed, "#close-compound")
    def close_compound(self) -> None:
        self.close()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.close()
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

    #open-compound {
        width: 100%;
    }
    """

    BORDER_TITLE = "Queries"

    BINDINGS = [
        ("ctrl+f", "open_filter", "Find query field"),
        ("ctrl+g", "open_compound_builder", "Compound query")
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
                yield Button("Compound", id="open-compound")
                yield CompoundQueryBuilder(field_ids, id="compound-builder", classes="hidden")
                yield ListFilter(field_ids, id="query-filter", classes="hidden")
                for _, field in search_fields.data.iterrows():
                    yield Input(placeholder=field["columnId"], id=field["columnId"])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if has_ancestor(event.input, ListFilter) or has_ancestor(event.input, CompoundQueryBuilder):
            return

        query_string = ""
        for input_field in self.query(Input).results():
            if has_ancestor(input_field, ListFilter) or has_ancestor(input_field, CompoundQueryBuilder):
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
        if (
                isinstance(focused, Input)
                and not has_ancestor(focused, ListFilter)
                and not has_ancestor(focused, CompoundQueryBuilder)
        ):
            results_table = self.app.query_one(SearchResults).query_one_optional(DataTable)
            if results_table is not None:
                results_table.focus()
                event.stop()
                event.prevent_default()

    @on(Button.Pressed, "#open-compound")
    def open_compound_button(self) -> None:
        self.action_open_compound_builder()

    def action_open_filter(self) -> None:
        focused = self.app.focused
        if isinstance(focused, Widget) and has_ancestor(focused, CompoundQueryBuilder):
            return
        self.query_one("#query-filter", ListFilter).open()

    def action_open_compound_builder(self) -> None:
        focused = self.app.focused
        field = None
        value = None
        if (
                isinstance(focused, Input)
                and focused.id is not None
                and not has_ancestor(focused, ListFilter)
                and not has_ancestor(focused, CompoundQueryBuilder)
        ):
            field = focused.id
            value = focused.value
        self.query_one("#compound-builder", CompoundQueryBuilder).open_with_clause(field, value)

    def on_list_filter_selected(self, event: ListFilter.Selected) -> None:
        event.stop()
        self.query_one("#query-filter", ListFilter).close()
        for input_field in self.query(Input).results():
            if input_field.id == event.value:
                input_field.focus()
                input_field.scroll_visible(immediate=True)
                return

    def on_compound_query_builder_applied(self, event: CompoundQueryBuilder.Applied) -> None:
        event.stop()
        self.queries = event.query


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
        ("ctrl+f", "open_filter", "Find return field")
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
