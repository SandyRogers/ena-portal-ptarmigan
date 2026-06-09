from enum import Enum
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel

from textual import log

from .config import app_config


class DataPortalEnum(str, Enum):
    ENA = "ena"
    METAGENOME = "metagenome"
    PATHOGEN = "pathogen"
    FAANG = "faang"


class FormatEnum(str, Enum):
    TSV = "tsv"
    JSON = "json"


class AppState(BaseModel):
    data_portal: DataPortalEnum
    format: FormatEnum


class CachedAppState:
    _default_state = AppState(data_portal=DataPortalEnum.ENA, format=FormatEnum.TSV)

    def __init__(self):
        self._state_file = Path(app_config.cache.cache_dir) / "state.json"
        if not self._state_file.exists():
            log("Making new default app state")
            self._write_state(self._default_state)

    @property
    def state(self) -> AppState:
        try:
            state = AppState.model_validate_json(self._state_file.read_text())
        except (OSError, ValueError):
            state = self._default_state.model_copy()
            self._write_state(state)
        log("Current app state is ", state)
        return state

    def update_state(self, key, value):
        state = self.state
        setattr(state, key, value)
        self._write_state(state)

    def _write_state(self, state: AppState) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            dir=self._state_file.parent,
            prefix="state-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(state.model_dump(mode="json"), temporary_file)
            temporary_path = temporary_file.name
        os.replace(temporary_path, self._state_file)
