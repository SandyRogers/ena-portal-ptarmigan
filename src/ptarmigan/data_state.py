from datetime import datetime
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
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
    error: Optional[str] = None


def get_data(endpoint: str, use_cache: bool = True) -> CachedDataset:
    url = get_endpoint_url(endpoint)
    cache_file = _cache_file(url)

    log(f"Will fetch data from {url}")

    if use_cache and cache_file.exists():
        try:
            log(f"Using cached dataset for {url}")
            return _read_cache_file(cache_file)
        except (KeyError, OSError, TypeError, ValueError):
            cache_file.unlink(missing_ok=True)

    try:
        response = httpx.get(url, timeout=app_config.ena.timeout)
        response.raise_for_status()
    except httpx.HTTPError as error:
        log(f"Request failed for {url}: {error}")
        return CachedDataset(
            data=pd.DataFrame(),
            cached_at=None,
            error=f"ENA API request failed: {error}",
        )

    log(f"Got response {response.status_code} for {url}")

    try:
        df = _dataframe_from_json(response.json())
    except (json.JSONDecodeError, TypeError, ValueError):
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
    return _data_cache_dir() / f"{sha256(url.encode()).hexdigest()}.json"


def _write_cache_file(cache_file: Path, dataset: CachedDataset) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=cache_file.parent,
        prefix=f"{cache_file.stem}-",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        json.dump(
            {
                "cached_at": dataset.cached_at.isoformat() if dataset.cached_at else None,
                "data": json.loads(dataset.data.to_json(orient="table", date_format="iso")),
            },
            temporary_file,
        )
        temporary_path = temporary_file.name
    os.replace(temporary_path, cache_file)


def _read_cache_file(cache_file: Path) -> CachedDataset:
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    dataframe = pd.read_json(
        StringIO(json.dumps(payload["data"])),
        orient="table",
    )
    cached_at = (
        datetime.fromisoformat(payload["cached_at"])
        if payload["cached_at"]
        else None
    )
    return CachedDataset(data=dataframe, cached_at=cached_at)


def _dataframe_from_json(payload) -> pd.DataFrame:
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    if isinstance(payload, dict):
        try:
            return pd.DataFrame(payload)
        except ValueError:
            return pd.DataFrame([payload])
    raise TypeError(f"Unsupported JSON response type: {type(payload).__name__}")
