from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
SPARQL endpoint reconciliation
==============================
Builds a :class:`~morph_kgc.reconciliation.index.ConceptIndex` from the answer
of a SPARQL endpoint to a SELECT query of the mapping. The query may read any
knowledge graph, and projects two variables:

- ``?entity_iri``, the entity a value is reconciled with.
- ``?matching_value_1``, the value the entity is matched by.

For instance::

    PREFIX schema: <https://schema.org/>
    SELECT ?entity_iri ?matching_value_1 WHERE {
        GRAPH ?g {
            ?entity_iri a schema:MedicalStudy ;
                        schema:identifier ?matching_value_1 .
        }
    }

The query is sent once, during the context initialization phase, and exactly as
written: whether its patterns read the named graphs of the endpoint is up to the
query, since stores differ on whether their default graph includes them. Values
are matched exactly; a query may normalize the values it returns (``LCASE``),
and the mapping the values it reconciles, to match otherwise.

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

from .._version import __version__
from ..constants import LOGGING_NAMESPACE
from ..http import fetch
from .index import ConceptIndex

LOGGER = logging.getLogger(LOGGING_NAMESPACE)

SPARQL_RESULTS_JSON = "application/sparql-results+json"

# The variables the query projects.
ENTITY_VARIABLE   = "entity_iri"
MATCHING_VARIABLE = "matching_value_1"
MATCHING_VARIABLES = re.compile(r"matching_value_\d+")

# Options a SPARQL_ENDPOINT resource declared when it also held the query, which
# the mapping now gives.
OBSOLETE_OPTIONS = {
    "query",
    "concept_variable",
    "attribute_variable",
    "value_variable",
    "attributes",
    "matching",
}

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


def run_query(resource, query: str) -> tuple[list[str] | None, list[dict]]:
    """
    Send *query* to the endpoint of *resource* and return the variables it
    projects, if the endpoint says, and its bindings.
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
        response = fetch(
            endpoint,
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
        # Not wrapped by fetch: the endpoint took too long to answer the query,
        # or dropped the connection while answering it.
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

    try:
        results = json.loads(response.body)
        bindings = results["results"]["bindings"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            f"The SPARQL endpoint '{endpoint}' did not answer with SPARQL results "
            f"in JSON ({response.content_type or 'no content type'}): {exc}. The "
            "query must be a SELECT query."
        ) from exc

    # The head is mandatory in SPARQL results, but an endpoint omitting it is
    # not worth failing for: the bindings then speak for the projection.
    head = results.get("head")
    variables = head.get("vars") if isinstance(head, dict) else None

    return variables, bindings


def _error_body(error: HTTPError) -> bytes:
    """The body of an error answer, decompressed as fetch does a successful one."""
    body = error.read()
    if error.headers.get("Content-Encoding", "").lower() != "gzip":
        return body
    try:
        return gzip.decompress(body)
    except (OSError, EOFError):
        return b""


def check_variables(endpoint: str, variables) -> None:
    """
    Make sure the query projects the variables reconciliation reads, rather than
    letting a misspelled one silently reconcile every value against nothing.
    """
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
            f"projects: {' '.join(f'?{variable}' for variable in variables) or 'nothing'}."
        )

    unsupported = [
        variable
        for variable in variables
        if MATCHING_VARIABLES.fullmatch(variable) and variable != MATCHING_VARIABLE
    ]
    if unsupported:
        raise ValueError(
            f"The SPARQL query sent to '{endpoint}' projects "
            f"{' '.join(f'?{variable}' for variable in unsupported)}, but a value "
            f"is only matched against ?{MATCHING_VARIABLE}. To match a value "
            "against several properties, bind them all to "
            f"?{MATCHING_VARIABLE}, e.g. with a UNION."
        )


def build_index(resource, query: str) -> ConceptIndex:
    """Build the index of *query* sent to the endpoint of *resource*."""
    endpoint = resource.get_url()
    variables, bindings = run_query(resource, query)
    if variables is not None:
        check_variables(endpoint, variables)

    index = ConceptIndex()
    entities = set()
    not_iris = 0

    for binding in bindings:
        entity = binding.get(ENTITY_VARIABLE)
        value  = binding.get(MATCHING_VARIABLE)
        # Unbound in this solution, e.g. by an OPTIONAL pattern.
        if entity is None or value is None:
            continue
        # A blank node or a literal identifies no entity outside the answer.
        if entity.get("type") != "uri":
            not_iris += 1
            continue
        # The answer says nothing of attributes: the values are indexed under
        # a single, unnamed one.
        index.add("", value["value"], entity["value"])
        entities.add(entity["value"])

    if not_iris:
        LOGGER.warning(
            f"The SPARQL query sent to '{endpoint}' bound ?{ENTITY_VARIABLE} to "
            f"something other than an IRI in {not_iris} solution(s), which were "
            "left out."
        )

    if entities:
        LOGGER.info(
            f"SPARQL endpoint '{endpoint}': indexed {len(entities)} entity(ies) "
            f"by {len(index)} value(s)."
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
