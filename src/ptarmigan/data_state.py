import shelve
from datetime import datetime
from io import StringIO
from typing import Optional

import httpx
import pandas as pd
from pydantic import BaseModel, ConfigDict
from textual import log

from .config import app_config


class CachedDataset(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    cached_at: Optional[datetime]
    data: pd.DataFrame


def get_data(endpoint: str, use_cache: bool = True) -> CachedDataset:
    with shelve.open(app_config.cache.cache_file + '-data', writeback=True) as cache:
        url = get_endpoint_url(endpoint)

        log(f"Will fetch data from {url}")

        if use_cache and url in cache:
            log(f"Using cached dataset for {url}")
            return cache[url]

        response = httpx.get(url)
        log(f"Got response {response.status_code} for {url}")

        try:
            df = pd.read_json(response.json())
        except:
            try:
                df = pd.read_csv(StringIO(response.text), sep="\t")
            except:
                df = pd.DataFrame()

        if use_cache:
            cache[url] = CachedDataset(data=df, cached_at=datetime.now())
            return cache[url]
        return CachedDataset(data=df, cached_at=None)


def get_endpoint_url(endpoint):
    url = str(app_config.ena.api_url_prefix)
    if not url[-1] == "/":
        url += "/"
    url += endpoint
    return url


def clear_cache():
    with shelve.open(app_config.cache.cache_file + '-data', writeback=True) as cache:
        cache.clear()
