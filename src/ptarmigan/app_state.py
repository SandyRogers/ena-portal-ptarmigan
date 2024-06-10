from enum import Enum

from pydantic import BaseModel
import shelve

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
    def __init__(self):
        with shelve.open(app_config.cache.cache_file + '-state', writeback=True) as cache:
            if 'app_state' not in cache:
                log("Making new default app state")
                cache['app_state'] = AppState(data_portal=DataPortalEnum.ENA, format=FormatEnum.TSV)
            else:
                log("App state exists in cache as", cache['app_state'])

    @property
    def state(self) -> AppState:
        with shelve.open(app_config.cache.cache_file + '-state') as cache:
            log("Current app state is ", cache['app_state'])
            return cache['app_state']

    def update_state(self, key, value):
        with shelve.open(app_config.cache.cache_file + '-state', writeback=True) as cache:
            state = cache['app_state']
            setattr(state, key, value)

