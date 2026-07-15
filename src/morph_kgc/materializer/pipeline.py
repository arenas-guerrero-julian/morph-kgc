from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
Layout
------
Section A — Group-level materialization
    materialize_rule()              one rule  -> set[str] of N-Triples/N-Quads
    materialize_group_to_set()      one partition -> set[str]
    materialize_group_to_file()     one partition -> int  (triples written)

Section B — Top-level pipeline orchestration
    _parse_mappings()               parse + normalize RML mapping
    _asserted_groups()              filter non-asserted triples maps
    _collect()                      flatten list[set] -> set
    _triples_to_graph()             set -> rdflib.Graph
    _triples_to_oxigraph()          set -> pyoxigraph.Store
    materialize_pipeline()                  full pipeline, returns Graph | Store | set
"""

import logging
from io import BytesIO

from rdflib import Graph

from ..config.loaders import load_config
from ..constants import (
    LOGGING_NAMESPACE,
    RML_ASSERTED_TRIPLES_MAP_CLASS,
    RML_TRIPLES_MAP_CLASS,
)
from ..mapping.parser import MappingParser
from ..mapping.model import RMLMapping, RMLRule
from .executor import Executor, make_executor
from .stages.load import load_data
from .stages.references import collect_references
from .stages.terms import materialize_terms
from .stages.join import merge_data
from .stages.assemble import assemble_triples

LOGGER = logging.getLogger(LOGGING_NAMESPACE)

# =============================================================================
# Section A — Group-level materialization
# =============================================================================

def materialize_rule(
    rule: RMLRule,
    rml_mapping: RMLMapping,
    config,
    python_source=None,
    nest_level: int = 0,
) -> set[str]:
    """
    Materialize one RML rule into a set of N-Triples / N-Quads strings.

    Parameters
    ----------
    rule:
        The RMLRule being processed.
    rml_mapping:
        Full parsed RMLMapping (needed for join resolution and FNML).
    config:
        Morph-KGC configuration object.
    python_source:
        Optional in-memory data for PYTHON_SOURCE logical sources.
    nest_level:
        Recursion depth for quoted-triple (triple-term) rules. At level 0
        the graph component is appended for N-Quads output.

    Returns
    -------
    set[str]
        Each element is a complete, serialized N-Triple / N-Quad line
        (without a trailing newline).
    """
    references = collect_references(rule, rml_mapping)
    data = load_data(config, rule, references, python_source)

    if data.empty:
        return set()

    om = rule.object_
    if om is not None and om.join_conditions:
        parent_rule = rml_mapping.get_rule(om.map_value)
        parent_refs = collect_references(parent_rule, rml_mapping, only_subject_map=True)
        parent_data = load_data(config, parent_rule, parent_refs, python_source)
        data = merge_data(data, parent_data, om.join_conditions)
        data = materialize_terms(data, rule, rml_mapping, config, columns_alias="parent_")
    else:
        data = materialize_terms(data, rule, rml_mapping, config)

    data = assemble_triples(data, rule, rml_mapping, config, nest_level=nest_level)

    return set(data["triple"].dropna().unique())


def materialize_group_to_set(
    group: list[RMLRule],
    rml_mapping: RMLMapping,
    config,
    python_source=None,
) -> set[str]:
    """Materialize one mapping partition into a set of N-Triple/N-Quad strings."""
    triples: set[str] = set()
    for rule in group:
        triples |= materialize_rule(rule, rml_mapping, config, python_source)
    return triples


def materialize_group_to_file(
    group: list[RMLRule],
    rml_mapping: RMLMapping,
    config,
    python_source=None,
) -> int:
    """
    Materialize one mapping partition and write results directly to the
    output file configured in *config*. Returns the number of triples written.
    """
    triples = materialize_group_to_set(group, rml_mapping, config, python_source)
    if not triples:
        return 0

    output_path = config.get_output_file_path()
    separator = "\n"

    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(separator.join(triples) + separator)

    return len(triples)


# =============================================================================
# Section B — Top-level pipeline orchestration
# =============================================================================

def _parse_mappings(config) -> RMLMapping:
    """Parse, normalize, and partition mapping rules."""
    parser = MappingParser(config)
    return parser.parse_mappings()


def _asserted_groups(rml_mapping: RMLMapping) -> list[list[RMLRule]]:
    """
    Return only the rule groups whose triples_map_type indicates asserted triples.

    In RML 1.2:
      - rml:TriplesMap and rml:AssertedTriplesMap  -> asserted (included)
      - rml:NonAssertedTriplesMap                  -> triple-term only (excluded)

    Groups are already built by the partitioner; we filter at the group level
    so that non-asserted rules are available for triple-term resolution inside
    materialize_group_* but never trigger their own top-level materialisation.
    """
    asserted_types = {RML_TRIPLES_MAP_CLASS, RML_ASSERTED_TRIPLES_MAP_CLASS}
    groups: dict[str, list[RMLRule]] = {}

    for rule in rml_mapping.rules:
        if rule.triples_map_type in asserted_types:
            groups.setdefault(rule.mapping_partition, []).append(rule)

    return list(groups.values())


def _collect(results: list) -> set[str]:
    """Flatten a list of sets into one set."""
    out: set[str] = set()
    for r in results:
        if isinstance(r, set):
            out |= r
    return out


def _triples_to_graph(triples: set[str]) -> Graph:
    graph = Graph()
    if triples:
        graph.parse(data=".\n".join(triples) + ".", format="nquads")
    return graph


def _triples_to_oxigraph(triples: set[str]):
    from pyoxigraph import Store
    store = Store()
    if triples:
        store.bulk_load(
            BytesIO((".\n".join(triples) + ".").encode()),
            "application/n-quads",
        )
    return store


def materialize_pipeline(
    config,
    python_source=None,
    executor: Executor | None = None,
    output: str = "graph",
):
    """
    Full materialization pipeline.

    Parameters
    ----------
    config:
        Path, dict, or ConfigParser.
    python_source:
        Optional in-memory data structure for PYTHON_SOURCE logical sources.
    executor:
        Explicit Executor instance. When None, make_executor(config) decides
        between Sequential, Multiprocess, or Async based on config.
    output:
        One of ``"graph"`` | ``"oxigraph"`` | ``"set"`` | ``"file"``.

    Returns
    -------
    Graph     when output="graph" (default)
    Store     when output="oxigraph"
    set[str]  when output="set"
    int       when output="file"  (total triple count)
    """

    # ── 2. Mappings ───────────────────────────────────────────────────────
    LOGGER.info("Parsing and normalising mapping rules.")
    rml_mapping = _parse_mappings(config)

    # ── 3 & 4. Partition + filter asserted groups ─────────────────────────
    groups = _asserted_groups(rml_mapping)
    LOGGER.info(f"{len(groups)} mapping group(s) to materialize.")

    # ── 5. Executor ───────────────────────────────────────────────────────
    if executor is None:
        executor = make_executor(config)

    LOGGER.debug(f"Using executor: {type(executor).__name__}.")

    # ── 6. Materialize + Serialize ────────────────────────────────────────
    if output == "file":
        results = executor.run(
            groups, materialize_group_to_file, rml_mapping, config
        )
        total = sum(r for r in results if isinstance(r, int))
        LOGGER.info(f"{total} triples written to file.")
        return total

    # "set", "graph", "oxigraph" — all collect into a set first
    results = executor.run(
        groups, materialize_group_to_set, rml_mapping, config,
        python_source=python_source,
    )
    triples = _collect(results)
    LOGGER.info(f"{len(triples)} triples generated in total.")

    if output == "set":
        return triples
    if output == "oxigraph":
        return _triples_to_oxigraph(triples)
    return _triples_to_graph(triples)
