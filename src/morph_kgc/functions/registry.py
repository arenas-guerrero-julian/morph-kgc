from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
Function Registry
=================
Single source of truth for all available functions (built-in + UDFs).

FunctionRegistry.get(function_iri, config) -> (callable, dict)
  Returns the function and its decorator-declared parameter mapping.

Built-in functions are registered via the @bif decorator.
UDFs are loaded on first access and cached.
"""

from collections.abc import Callable

from .udf_loader import load_udfs
from .bif_decorator import *

from .grel import *     # needed to populate bif_dict


class FunctionRegistry:
    """Lazy-loading registry for built-in functions and UDFs."""

    _udf_cache: dict | None = None

    @classmethod
    def get(cls, function_iri: str, config) -> tuple[Callable, dict]:
        """
        Return (function, decorator_parameters) for *function_iri*.
        Raises KeyError if the function is not found in built-ins or UDFs.
        """
        if function_iri in bif_dict:
            entry = bif_dict[function_iri]
            return entry["function"], entry["parameters"]

        if cls._udf_cache is None:
            cls._udf_cache = load_udfs(config)

        entry = cls._udf_cache[function_iri]
        return entry["function"], entry["parameters"]
