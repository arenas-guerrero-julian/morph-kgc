__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
morph-kgc CLI entry point
==========================
Invoked as::

    python -m morph_kgc <config.ini>

Flow
----
1. ``config.load_from_cli()`` parses sys.argv (argparse lives in
   ``config/loaders.py``) and returns a validated ``MorphConfig``.
2. Optional pyjelly dependency is validated eagerly for JELLY output.
3. Mappings are loaded via ``mapping.parser.retrieve_mappings``.
4. Output files/directories are wiped via ``utils.io.prepare_output_files``.
5. An Executor is chosen via ``execution.executor.make_executor``.
6. The materialization pipeline runs via ``execution.pipeline.run_pipeline``.
7. Timing + triple count are logged.

JELLY note
----------
pyjelly cannot stream triples to an append-only file in parallel chunks;
it needs a complete ``rdflib.Graph`` serialised in one shot.  The JELLY path
therefore forces ``number_of_processes=1``, materialises to a set, builds
an rdflib Graph, and serialises via ``graph.serialize(format="jelly")``.
"""

import logging
import sys
import time

from .config                import load_from_cli
from .config.model          import MorphConfig
from .constants.misc        import LOGGING_NAMESPACE
from .constants.output      import JELLY, NQUADS
from .execution.executor    import make_executor
from .execution.pipeline    import run_pipeline
from .mapping.parser        import retrieve_mappings
from .utils.io              import prepare_output_files, create_dirs_in_path
from .utils.time            import get_delta_time

LOGGER = logging.getLogger(LOGGING_NAMESPACE)


# ── JELLY-specific helpers ────────────────────────────────────────────────────

def _assert_pyjelly_available() -> None:
    """Raise a helpful RuntimeError when pyjelly[rdflib] is not installed."""
    try:
        import pyjelly  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "JELLY output requested but pyjelly[rdflib] is not installed. "
            "Install it with:  pip install \'morph-kgc[jelly]\'"
        ) from exc


def _run_jelly(config: MorphConfig) -> None:
    """
    Materialise to a temporary set, build an rdflib Graph, serialise as Jelly.

    Must be single-process: pyjelly's serialiser is not process-safe.
    """
    from rdflib import Graph

    _assert_pyjelly_available()

    # Force single-process for pyjelly safety.
    object.__setattr__(config, "number_of_processes", 1)

    rml_mapping, fnml_mapping, http_api_df = retrieve_mappings(config)
    config.set_http_api_df_csv(http_api_df.to_csv())

    triples: set[str] = run_pipeline(
        config,
        rml_mapping=rml_mapping,
        fnml_mapping=fnml_mapping,
        output="set",
    )

    rdf_format = "nquads" if config.output_format == NQUADS else "ntriples"
    graph = Graph()
    if triples:
        graph.parse(data=".
".join(triples) + ".", format=rdf_format)

    output_path = config.get_output_file_path()
    create_dirs_in_path(output_path)
    graph.serialize(destination=output_path, format="jelly")
    LOGGER.info("Jelly file written to: %s", output_path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # load_from_cli() owns argparse: parses sys.argv, reads the INI file,
    # validates all fields, configures logging.
    config: MorphConfig = load_from_cli()

    # ── JELLY: single-shot serialization path ────────────────────────────────
    if config.output_format == JELLY:
        start = time.time()
        _run_jelly(config)
        LOGGER.info("Materialization finished in %s seconds.", get_delta_time(start))
        return

    # ── Standard path: file or Kafka output ──────────────────────────────────
    rml_mapping, fnml_mapping, http_api_df = retrieve_mappings(config)
    config.set_http_api_df_csv(http_api_df.to_csv())

    prepare_output_files(config, rml_mapping)

    executor = make_executor(config)
    output   = "kafka" if config.get_output_kafka_server() else "file"

    start = time.time()
    result = run_pipeline(
        config,
        rml_mapping=rml_mapping,
        fnml_mapping=fnml_mapping,
        executor=executor,
        output=output,
    )

    num_triples = result if isinstance(result, int) else 0
    LOGGER.info("Number of triples generated in total: %d.", num_triples)
    LOGGER.info("Materialisation finished in %s seconds.", get_delta_time(start))


if __name__ == "__main__":
    main()
