from datetime import datetime
from hashlib import sha256
from io import StringIO
import os
from pathlib import Path
import pickle
import shutil
from tempfile import NamedTemporaryFile
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
    url = get_endpoint_url(endpoint)
    cache_file = _cache_file(url)

    log(f"Will fetch data from {url}")

    if use_cache and cache_file.exists():
        try:
            log(f"Using cached dataset for {url}")
            with cache_file.open("rb") as cached_file:
                return pickle.load(cached_file)
        except (OSError, pickle.PickleError, EOFError):
            cache_file.unlink(missing_ok=True)

    response = httpx.get(url, timeout=app_config.ena.timeout)
    response.raise_for_status()
    log(f"Got response {response.status_code} for {url}")

    try:
        df = pd.read_json(response.json())
    except (TypeError, ValueError):
        try:
            df = pd.read_csv(StringIO(response.text), sep="\t")
        except (pd.errors.ParserError, pd.errors.EmptyDataError):
            df = pd.DataFrame()

    dataset = CachedDataset(
        data=df,
        cached_at=datetime.now() if use_cache else None,
    )
    if use_cache:
        _write_cache_file(cache_file, dataset)
    return dataset


def get_endpoint_url(endpoint):
    url = str(app_config.ena.api_url_prefix)
    if not url[-1] == "/":
        url += "/"
    url += endpoint
    return url


def clear_cache():
    shutil.rmtree(_data_cache_dir(), ignore_errors=True)


def _data_cache_dir() -> Path:
    return Path(app_config.cache.cache_dir) / "data"


def _cache_file(url: str) -> Path:
    return _data_cache_dir() / f"{sha256(url.encode()).hexdigest()}.pickle"


def _write_cache_file(cache_file: Path, dataset: CachedDataset) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb",
        dir=cache_file.parent,
        prefix=f"{cache_file.stem}-",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        pickle.dump(dataset, temporary_file, protocol=pickle.HIGHEST_PROTOCOL)
        temporary_path = temporary_file.name
    os.replace(temporary_path, cache_file)
