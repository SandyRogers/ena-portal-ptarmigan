from pandas import DataFrame
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Button


class PandasTable(Widget):
    DEFAULT_CSS = """
    PandasTable {
        height: 100%
    }
    Horizontal {
        align: center top
    }
    """

    def __init__(self, df: DataFrame, **kwargs):
        self.df = df
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield DataTable()
        with Horizontal():
            yield Button("More", id="more")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(*tuple(self.df.columns.values.tolist()))
        self.query_one(DataTable).add_rows(self.df.itertuples(index=False, name=None))
