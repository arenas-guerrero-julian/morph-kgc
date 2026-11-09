from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
SPARQL endpoint reconciliation
==============================
Builds a :class:`~morph_kgc.reconciliation.index.ConceptIndex` from the answer
of a SPARQL endpoint to a SELECT query of the mapping. The query may read any
knowledge graph, and projects:

- ``?entity_iri``, the entity a value is reconciled with.
- ``?matching_value_1``, the value the entity is matched by, and, for an entity
  matched by several values together, ``?matching_value_2`` and so on.

For instance::

    PREFIX schema: <https://schema.org/>
    SELECT ?entity_iri ?matching_value_1 WHERE {
        GRAPH ?g {
            ?entity_iri a schema:MedicalStudy ;
                        schema:identifier ?matching_value_1 .
        }
    }

An entity identified by a code within a coding system, which only the two
together identify, is matched by both::

    PREFIX schema: <https://schema.org/>
    SELECT ?entity_iri ?matching_value_1 ?matching_value_2 WHERE {
        GRAPH ?g {
            ?entity_iri a schema:MedicalCode ;
                        schema:codeValue ?matching_value_1 ;
                        schema:codingSystem ?matching_value_2 .
        }
    }

The execution of the mapping then binds as many values, matched to the
variables by position (``grel:valueParam1``, or ``grel:valueParam``, to
``?matching_value_1``, ``grel:valueParam2`` to ``?matching_value_2``...), and
a row is reconciled with the entities matched by all its values, compared one
by one: each is a column of the index, never joined with the others into a
string, which could make different tuples of values look alike.

The query is sent once, during the context initialization phase, and exactly as
written. Its answer is downloaded to the state directory of the run and read as
a stream into the index on disk, so that an answer of any size is indexed with
the same memory. An answer in JSON is only read as a stream when it gives its
head first, as most stores write it, and when every term in it is well formed;
otherwise it is read whole into memory, with a warning. An answer in XML, which
some endpoints send whatever is asked for, is read as a stream too, keeping the
text of its literals exactly as written, whitespace around it included. Whether its patterns
read the named graphs of the endpoint is up to the query, since stores differ on
whether their default graph includes them. Values are matched exactly; a query
may normalize the values it returns (``LCASE``), and the mapping the values it
reconciles, to match otherwise.

How the endpoint is accessed is declared in the configuration file, only when
its URL is not enough: a ``[RESOURCE:<name>]`` section whose ``url`` (or
``iri``) is the endpoint named by the mapping::

    [RESOURCE:clinical_trials]
    resource_type=SPARQL_ENDPOINT
    url=https://example.org/sparql
    username={ENDPOINT_USER}
    password={ENDPOINT_PASSWORD}
    method=POST
"""

import gzip
import json
import logging
import re
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from xml.etree.ElementTree import iterparse

import pyoxigraph

from .._version import __version__
from ..constants import GREL_VALUE_PARAM, LOGGING_NAMESPACE
from ..http import Download, download
from .index import ConceptIndex, ConceptIndexBuilder

LOGGER = logging.getLogger(LOGGING_NAMESPACE)

SPARQL_RESULTS_JSON = "application/sparql-results+json"

# The serializations of SPARQL results read as a stream, by content type. JSON
# is the one asked for; the others are read too, should an endpoint send them.
# XML is read by the parser of the standard library rather than by pyoxigraph,
# which trims the whitespace a literal starts or ends with unless the endpoint
# escapes it ('&#32;'), as rdflib and most stores do not.
RESULTS_FORMATS = {
    SPARQL_RESULTS_JSON:               pyoxigraph.QueryResultsFormat.JSON,
    "application/json":                pyoxigraph.QueryResultsFormat.JSON,
    "application/sparql-results+xml":  pyoxigraph.QueryResultsFormat.XML,
    "application/xml":                 pyoxigraph.QueryResultsFormat.XML,
    "text/xml":                        pyoxigraph.QueryResultsFormat.XML,
    "text/tab-separated-values":       pyoxigraph.QueryResultsFormat.TSV,
}

# The variables the query projects: the entity, and the values it is matched by,
# numbered from 1.
ENTITY_VARIABLE   = "entity_iri"
MATCHING_VARIABLE_PREFIX = "matching_value_"
MATCHING_VARIABLE = f"{MATCHING_VARIABLE_PREFIX}1"
MATCHING_VARIABLES = re.compile(rf"{MATCHING_VARIABLE_PREFIX}(\d+)")

VALID_METHODS = {"GET", "POST"}

# Unless the resource says otherwise, a query is sent with GET, the method every
# endpoint supports, but a query too long for a URL is sent with POST.
MAX_GET_URL_LENGTH = 4096

# The query is sent once, over a whole knowledge graph, so it is given time.
DEFAULT_TIMEOUT = 300

# Some endpoints, such as the Wikidata Query Service, refuse anonymous clients.
USER_AGENT = f"morph-kgc/{__version__}"

# How much of an error answered by the endpoint (e.g. a syntax error in the
# query) is reported.
ERROR_EXCERPT_LENGTH = 500


def get_method(resource, url: str, query: str) -> str:
    method = resource.get("method").strip().upper()
    if not method:
        get_url = f"{url}{'&' if '?' in url else '?'}{urlencode({'query': query})}"
        return "GET" if len(get_url) <= MAX_GET_URL_LENGTH else "POST"

    if method not in VALID_METHODS:
        raise ValueError(
            f"Option 'method' of resource '{resource.name}' is '{method}', "
            f"which is not valid. Must be one of: {sorted(VALID_METHODS)}."
        )
    return method


def run_query(resource, query: str, state_dir: str) -> Download:
    """
    Send *query* to the endpoint of *resource* and download its answer to
    *state_dir*.
    """
    endpoint = resource.get_url()
    if urlsplit(endpoint).scheme.lower() not in ("http", "https"):
        raise ValueError(
            f"'{endpoint or resource.name}' is not the URL of a SPARQL endpoint "
            "to reconcile against: it must be an http(s) URL."
        )

    method = get_method(resource, endpoint, query)

    if method == "POST":
        request = {
            "data": urlencode({"query": query}).encode("utf-8"),
            "content_type": "application/x-www-form-urlencoded",
        }
    else:
        request = {"params": {"query": query}}

    try:
        return download(
            endpoint,
            state_dir,
            username    = resource.get_username(),
            password    = resource.get_password(),
            accept      = SPARQL_RESULTS_JSON,
            headers     = {"User-Agent": USER_AGENT},
            method      = method,
            timeout     = resource.get_int("timeout", DEFAULT_TIMEOUT),
            description = "SPARQL endpoint",
            **request,
        )
    except (OSError, HTTPException) as exc:
        # Not wrapped by download: the endpoint took too long to answer the
        # query, or dropped the connection while answering it.
        detail = str(exc) or type(exc).__name__
        if not isinstance(exc, TimeoutError):
            raise ValueError(
                f"Could not query the SPARQL endpoint '{endpoint}': {detail}."
            ) from exc
        raise ValueError(
            f"The SPARQL endpoint '{endpoint}' did not answer the query: {detail}. "
            "If the query needs longer, declare the seconds to wait for it as "
            "'timeout' in the configuration file, in a '[RESOURCE:<name>]' "
            f"section with 'resource_type=SPARQL_ENDPOINT' and 'url={endpoint}'."
        ) from exc
    except ValueError as exc:
        cause = exc.__cause__
        if not isinstance(cause, HTTPError):
            raise

        if cause.code in (401, 403) and not resource.has_credentials():
            # The endpoint may well be named by the mapping only, so the
            # configuration file may have no section to set credentials in yet.
            raise ValueError(
                f"The SPARQL endpoint '{endpoint}' denied access ({cause.code} "
                f"{cause.reason}). To access it with HTTP Basic Authentication, "
                "declare its 'username' and 'password' in the configuration "
                "file, in a '[RESOURCE:<name>]' section with "
                f"'resource_type=SPARQL_ENDPOINT' and 'url={endpoint}'."
            ) from exc

        # The endpoint says what is wrong with the query far better than the
        # status code does.
        detail = _error_body(cause).decode("utf-8", errors="replace").strip()
        if not detail:
            raise
        raise ValueError(
            f"The SPARQL endpoint '{endpoint}' answered {cause.code} "
            f"{cause.reason}: {detail[:ERROR_EXCERPT_LENGTH]}"
        ) from exc


def matching_variables(arity: int = 1) -> list[str]:
    """The variables matched against the *arity* values a row is reconciled by."""
    return [f"{MATCHING_VARIABLE_PREFIX}{position}" for position in range(1, arity + 1)]


def read_solutions(endpoint: str, answer: Download, arity: int = 1):
    """
    The variables the answer of *endpoint* in the file *answer* projects, if
    it says, and an iterator of its solutions, read as a stream: for each, the
    term bound to ?entity_iri, and the tuple of the terms bound to the *arity*
    variables ?matching_value_1, ?matching_value_2..., as (kind, value) pairs
    whose kind is ``uri`` for an IRI.
    """
    json_format = pyoxigraph.QueryResultsFormat.JSON
    results_format = RESULTS_FORMATS.get(answer.content_type, json_format)
    if results_format == pyoxigraph.QueryResultsFormat.XML:
        return _read_xml_solutions(endpoint, answer, arity)
    try:
        results = pyoxigraph.parse_query_results(path=answer.path, format=results_format)
    except SyntaxError as exc:
        if results_format != json_format:
            raise _not_results(endpoint, answer, exc) from exc
        # The head is mandatory in SPARQL results, but an endpoint omitting it
        # is not worth failing for: the bindings then speak for the projection.
        return _read_json_solutions(endpoint, answer, exc, arity)

    if not isinstance(results, pyoxigraph.QuerySolutions):
        raise _not_results(endpoint, answer, "it is the answer to an ASK query")

    if results_format == json_format and not _starts_with_head(answer.path):
        # The parser holds the solutions until it reads which variables they bind.
        LOGGER.warning(
            f"The answer of the SPARQL endpoint '{endpoint}' gives its results "
            "before its head, so it is read whole into memory before it is indexed."
        )

    variables = [variable.value for variable in results.variables]
    matched = matching_variables(arity)
    if any(variable not in variables for variable in (ENTITY_VARIABLE, *matched)):
        # check_variables says which is missing.
        return variables, iter(())
    # By position, much faster than by name over millions of solutions.
    entity = variables.index(ENTITY_VARIABLE)
    values = [variables.index(variable) for variable in matched]

    def solutions():
        try:
            for solution in results:
                yield (
                    _solution_term(solution[entity]),
                    tuple(_solution_term(solution[value]) for value in values),
                )
        except SyntaxError as exc:
            if results_format == json_format:
                # A term the streaming parser rejects and json.load may not: a
                # Virtuoso 'nodeID://' blank node, a language tag that is not
                # BCP 47, an IRI with a space.
                raise _StreamingError(exc) from exc
            raise _not_results(endpoint, answer, exc) from exc

    return variables, solutions()


class _StreamingError(Exception):
    """The streaming parser rejects a solution of an answer in JSON."""


def _starts_with_head(path: str) -> bool:
    """Whether the answer in JSON at *path* gives its head first."""
    with open(path, "rb") as answer_file:
        start = answer_file.read(4096)
    return re.match(rb'(\xef\xbb\xbf)?\s*\{\s*"head"', start) is not None


def _solution_term(term) -> tuple[str, str] | None:
    """The kind and value of a term bound in a solution, None when unbound."""
    if term is None:
        return None
    if isinstance(term, pyoxigraph.NamedNode):
        return "uri", term.value
    # A triple term has no value of its own to match.
    return type(term).__name__.lower(), getattr(term, "value", None)


def _read_json_solutions(endpoint: str, answer: Download, reason, arity: int = 1):
    """The solutions of an answer in JSON the streaming parser rejects, in memory."""
    try:
        with open(answer.path, "rb") as answer_file:
            results = json.load(answer_file)
        bindings = results["results"]["bindings"]
        iter(bindings)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise _not_results(endpoint, answer, exc) from exc

    LOGGER.warning(
        f"The answer of the SPARQL endpoint '{endpoint}' cannot be read as a "
        f"stream ({reason}), so it is read in memory."
    )

    head = results.get("head")
    variables = head.get("vars") if isinstance(head, dict) else None

    def term(binding):
        if not isinstance(binding, dict):
            return None
        return binding.get("type"), binding.get("value")

    matched = matching_variables(arity)

    def solutions():
        for binding in bindings:
            yield (
                term(binding.get(ENTITY_VARIABLE)),
                tuple(term(binding.get(variable)) for variable in matched),
            )

    return variables, solutions()


def _read_xml_solutions(endpoint: str, answer: Download, arity: int = 1):
    """
    The variables the answer in XML of *endpoint* projects, None if it has no
    head, and a generator of its solutions, which holds the answer open until
    it is exhausted or closed.
    """
    solutions = _parse_xml_solutions(endpoint, answer, arity)
    # Read up to the results, which the answer to an ASK query does not have.
    variables = next(solutions)
    return variables, solutions


def _parse_xml_solutions(endpoint: str, answer: Download, arity: int):
    """
    Read the answer in XML of *endpoint* as a stream: yield the variables it
    projects, as soon as its results start, and then each of its solutions,
    whose elements are forgotten once read so that memory stays flat. Elements
    are known by their local name, whatever their namespace.
    """
    matched = matching_variables(arity)
    variables = None
    started = False
    try:
        with open(answer.path, "rb") as answer_file:
            # The depth of the element an event is about, the root being 1, and
            # the element of the results, while they are read.
            depth = 0
            results = None
            for event, element in iterparse(answer_file, events=("start", "end")):
                if event == "start":
                    depth += 1
                    if depth > 2:
                        continue
                    name = _local_name(element.tag)
                    if depth == 1 and name != "sparql":
                        raise SyntaxError(f"its root element is <{name}>, not <sparql>")
                    if depth == 2 and name == "results":
                        results = element
                        if not started:
                            started = True
                            yield variables
                    continue

                depth -= 1
                if depth == 2 and results is not None:
                    if _local_name(element.tag) == "result":
                        bindings = {
                            binding.get("name"): binding
                            for binding in element
                            if _local_name(binding.tag) == "binding"
                        }
                        yield (
                            _xml_term(bindings.get(ENTITY_VARIABLE)),
                            tuple(_xml_term(bindings.get(variable)) for variable in matched),
                        )
                    results.remove(element)
                elif depth == 1:
                    name = _local_name(element.tag)
                    if name == "head":
                        variables = [
                            variable.get("name")
                            for variable in element
                            if _local_name(variable.tag) == "variable"
                            and variable.get("name") is not None
                        ]
                    elif name == "boolean":
                        raise SyntaxError("it is the answer to an ASK query")
                    elif name == "results":
                        results = None
    except (SyntaxError, ValueError, LookupError) as exc:
        # Also xml.etree.ElementTree.ParseError, for XML that is not well formed,
        # and the errors of an encoding the parser cannot decode.
        raise _not_results(endpoint, answer, exc) from exc

    if not started:
        # Neither results nor a boolean: no solution.
        yield variables


def _local_name(tag: str) -> str:
    """The name of an element without its namespace."""
    return tag.rpartition("}")[2]


def _xml_term(binding) -> tuple[str, str | None] | None:
    """
    The kind and value of the term a <binding> element binds, None when it
    binds none. The text of a literal is kept exactly; that of an IRI or a blank
    node, which cannot hold whitespace, is stripped of any around it.
    """
    if binding is None:
        return None
    for term in binding:
        kind = _local_name(term.tag)
        if kind == "triple":
            # A triple term has no value of its own to match.
            return kind, None
        if len(term):
            raise SyntaxError(f"<{kind}> holds the element <{_local_name(term[0].tag)}>, not text")
        value = term.text or ""
        return kind, value if kind == "literal" else value.strip()
    return None


def _not_results(endpoint: str, answer: Download, detail) -> ValueError:
    return ValueError(
        f"The SPARQL endpoint '{endpoint}' did not answer with SPARQL results "
        f"in JSON ({answer.content_type or 'no content type'}): {detail}. The "
        "query must be a SELECT query."
    )


def _error_body(error: HTTPError) -> bytes:
    """The body of an error answer, decompressed as fetch does a successful one."""
    body = error.read()
    if error.headers.get("Content-Encoding", "").lower() != "gzip":
        return body
    try:
        return gzip.decompress(body)
    except (OSError, EOFError):
        return b""


def check_variables(endpoint: str, variables, arity: int = 1) -> None:
    """
    Make sure the query projects the variables reconciliation reads, rather than
    letting a misspelled one silently reconcile every value against nothing:
    ?entity_iri, and one ?matching_value_<i> for each of the *arity* values the
    mapping matches entities by, numbered from 1.
    """
    projected = " ".join(f"?{variable}" for variable in variables) or "nothing"
    missing = [
        variable
        for variable in (ENTITY_VARIABLE, MATCHING_VARIABLE)
        if variable not in variables
    ]
    if missing:
        raise ValueError(
            f"The SPARQL query sent to '{endpoint}' does not project "
            f"{' and '.join(f'?{variable}' for variable in missing)}. It must "
            f"project ?{ENTITY_VARIABLE}, the entity a value is reconciled with, "
            f"and ?{MATCHING_VARIABLE}, the value the entity is matched by. It "
            f"projects: {projected}."
        )

    numbered = [
        variable for variable in variables if MATCHING_VARIABLES.fullmatch(variable)
    ]
    expected = matching_variables(len(numbered))
    if sorted(numbered) != sorted(expected):
        unexpected = [variable for variable in numbered if variable not in expected]
        raise ValueError(
            f"The SPARQL query sent to '{endpoint}' projects "
            f"{' '.join(f'?{variable}' for variable in unexpected)}, but the "
            f"values an entity is matched by are projected as "
            f"{', '.join(f'?{variable}' for variable in matching_variables(3))}... "
            "numbered from 1, without gaps or leading zeros. It projects: "
            f"{projected}."
        )

    if len(numbered) != arity:
        raise ValueError(
            f"The SPARQL query sent to '{endpoint}' matches entities by "
            f"{len(numbered)} value(s), "
            f"{' '.join(f'?{variable}' for variable in expected)}, but the "
            f"mapping reconciles {arity} value(s) with it. Bind one value to "
            "reconcile per variable, matched by position: "
            f"'{GREL_VALUE_PARAM}1' (or '{GREL_VALUE_PARAM}') to "
            f"?{MATCHING_VARIABLE}, '{GREL_VALUE_PARAM}2' to "
            f"?{MATCHING_VARIABLE_PREFIX}2, and so on."
        )


def _index_solutions(solutions, state_dir: str, memory_limit: str, arity: int = 1):
    """The index of *solutions*, and how many bound ?entity_iri to no IRI."""
    not_iris = 0
    with ConceptIndexBuilder(state_dir, memory_limit, arity=arity) as builder:
        for entity, values in solutions:
            # Unbound in this solution, e.g. by an OPTIONAL pattern: an entity
            # is only matched by all its values.
            if entity is None or any(value is None or value[1] is None for value in values):
                continue
            # A blank node or a literal identifies no entity outside the answer.
            if entity[0] != "uri":
                not_iris += 1
                continue
            # The answer says nothing of attributes: the values are indexed
            # under a single, unnamed one.
            matched = values[0][1] if arity == 1 else tuple(value[1] for value in values)
            builder.add("", matched, entity[1])
        return builder.build(), not_iris


def build_index(
    resource, query: str, *, state_dir: str, memory_limit: str, arity: int = 1,
) -> ConceptIndex:
    """
    Build the index of *query* sent to the endpoint of *resource*, in
    *state_dir*, matching entities by the *arity* values the mapping binds.
    """
    endpoint = resource.get_url()
    answer = run_query(resource, query, state_dir)
    solutions = None
    try:
        variables, solutions = read_solutions(endpoint, answer, arity)
        if variables is not None:
            check_variables(endpoint, variables, arity)

        try:
            index, not_iris = _index_solutions(solutions, state_dir, memory_limit, arity)
        except _StreamingError as exc:
            # Indexed anew from the start, by a new builder.
            _, solutions = _read_json_solutions(endpoint, answer, exc, arity)
            index, not_iris = _index_solutions(solutions, state_dir, memory_limit, arity)
    finally:
        # A reader holding the answer open, as that of answers in XML does until
        # it is exhausted, would keep Windows from removing it.
        if hasattr(solutions, "close"):
            solutions.close()
        answer.remove()

    if not_iris:
        LOGGER.warning(
            f"The SPARQL query sent to '{endpoint}' bound ?{ENTITY_VARIABLE} to "
            f"something other than an IRI in {not_iris} solution(s), which were "
            "left out."
        )

    if index.concept_count:
        by = "value(s)" if arity == 1 else f"tuple(s) of {arity} values"
        LOGGER.info(
            f"SPARQL endpoint '{endpoint}': indexed {index.concept_count} "
            f"entity(ies) by {len(index)} {by}."
        )
    else:
        hint = ""
        if "GRAPH" not in query.upper():
            hint = (
                " If the knowledge graph keeps its entities in named graphs, read "
                "them with a 'GRAPH ?g { ... }' pattern: the default graph of many "
                "stores does not include them."
            )
        LOGGER.warning(
            f"The SPARQL query sent to '{endpoint}' returned no entity, so no "
            f"value will be reconciled against it.{hint}"
        )

    return index
