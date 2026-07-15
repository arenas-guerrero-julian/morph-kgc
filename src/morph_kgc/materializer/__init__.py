__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
materializer
============
Unified submodule that replaces the former separate ``execution`` and
``materializer`` sub-packages.

Public surface
--------------
from morph_kgc.materializer import (
    materialize_set,
    materialize,
    materialize_oxigraph,
)

For advanced use (custom executors, output routing):

from morph_kgc.materializer.pipeline import run_pipeline
from morph_kgc.materializer.executor import make_executor, Executor
"""

from .pipeline import (
    materialize_pipeline,
)

__all__ = [
    "materialize_pipeline",
]
