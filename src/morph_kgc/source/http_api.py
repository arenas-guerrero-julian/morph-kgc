from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
HTTP API adapter
=================
Implements DataSourceAdapter for REST / HTTP API sources.

The source type string "HTTPAPI" is registered in source/__init__.py.
"""

import importlib.util
import os
import sys
from io import StringIO
from typing import Any

import pandas as pd
from jsonpath import JSONPath


def _load_module_from_path(module_name: str, file_path: str):
    """Dynamically load a Python module from *file_path*."""
    spec   = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _fetch_http_api(config, rml_rule, references: set[str]) -> pd.DataFrame:
    import requests

    http_api_df = pd.read_csv(StringIO(config.get("CONFIGURATION", "http_api_df")))
    df = http_api_df[http_api_df["source"] == rml_rule.logical_source.value]

    absolute_path = list(df["absolute_path"])[0]
    payload: dict = {}
    headers: dict = {}

    if "field_name" in df.columns:
        for _, row in df.iterrows():
            if row["field_name"] in os.environ:
                field_value = row["field_value"].format(**os.environ)
            else:
                mod = _load_module_from_path("dynamic_api_token", config.get_api_token())
                field_value = mod.get_api_token(arg1=row["field_value"])

            if row["field_name"].lower() in ["authorization", "accept", "keyid", "user-agent"]:
                headers[row["field_name"]] = field_value
            else:
                payload[row["field_name"]] = field_value

    json_data = requests.get(absolute_path, params=payload, headers=headers).json()

    def _has_filter(ref: str) -> bool:
        return "[?(" in ref

    simple_refs = [r for r in references if not _has_filter(r)]
    filter_refs = [r for r in references if _has_filter(r)]

    records = [
        {r: match.object for r in simple_refs
         for match in [next(iter(JSONPath(r).parse(item)), None)]
         if match is not None}
        for item in (json_data if isinstance(json_data, list) else [json_data])
    ]

    result_df = pd.DataFrame(records, columns=list(simple_refs))

    for fref in filter_refs:
        matches = [m.object for m in JSONPath(fref).parse(json_data)]
        if matches:
            result_df[fref] = matches[0] if len(matches) == 1 else str(matches)

    return result_df

class HttpApiAdapter:
    """DataSourceAdapter for HTTP API sources."""

    def get_data(
        self,
        config: Any,
        rml_rule: Any,
        references: set[str],
        python_source: dict | None = None,
    ) -> pd.DataFrame:
        return _fetch_http_api(config, rml_rule, references)