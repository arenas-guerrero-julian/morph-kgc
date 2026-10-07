from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
SKOS vocabulary resources
=========================
Builds a :class:`~morph_kgc.reconciliation.index.ConceptIndex` from a SKOS
vocabulary fetched from the URL declared by a ``[RESOURCE:<name>]`` section::

    [RESOURCE:disease_vocabulary]
    resource_type=SKOS_VOCABULARY
    url=https://example.org/vocabulary/disease
    username={VOCABULARY_USER}
    password={VOCABULARY_PASSWORD}
    format=turtle
    matching=CASE-INSENSITIVE
    attributes=skos:prefLabel,skos:altLabel
    timeout=60

The vocabulary is retrieved once, during the context initialization phase. It
may be serialized as triples or as quads (N-Quads, TriG): the concepts of every
graph of a vocabulary split across named graphs are indexed.

The ``attributes`` are absolute IRIs, or prefixed names with one of the
well-known prefixes in :data:`WELL_KNOWN_PREFIXES` (``skos:``, ``dcterms:``,
``schema:``, ...). An attribute in any other namespace is written as a full IRI.
"""

import logging
from urllib.parse import urlsplit

import rdflib
from rdflib.namespace import DC, DCTERMS, FOAF, OWL, RDF, RDFS, SDO, SKOS, XSD

from ..constants import LOGGING_NAMESPACE
from ..http import DEFAULT_TIMEOUT, fetch
from .index import ConceptIndex, EXACT_MATCHING, VALID_MATCHINGS

LOGGER = logging.getLogger(LOGGING_NAMESPACE)

# Prefixes the 'attributes' option may use. The prefixes a vocabulary declares
# are not used: rdflib renames them when they clash with its own bindings.
WELL_KNOWN_PREFIXES = {
    "skos":    str(SKOS),
    "rdf":     str(RDF),
    "rdfs":    str(RDFS),
    "owl":     str(OWL),
    "xsd":     str(XSD),
    "dc":      str(DC),
    "dcterms": str(DCTERMS),
    "dct":     str(DCTERMS),
    "schema":  str(SDO),
    "foaf":    str(FOAF),
}

# SKOS makes these classes disjoint from skos:Concept: their resources are
# labelled as concepts are, but no value identifies one of them as a concept.
NON_CONCEPT_CLASSES = (SKOS.ConceptScheme, SKOS.Collection, SKOS.OrderedCollection)

# Serializations we can ask for, most specific first.
ACCEPTED_MEDIA_TYPES = (
    "text/turtle, application/rdf+xml;q=0.9, application/n-triples;q=0.9, "
    "application/ld+json;q=0.8, application/n-quads;q=0.8, application/trig;q=0.8, "
    "text/n3;q=0.7, */*;q=0.1"
)

CONTENT_TYPE_FORMATS = {
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "application/rdf+xml": "xml",
    "text/rdf+xml": "xml",
    "application/n-triples": "nt",
    "application/n-quads": "nquads",
    "text/x-nquads": "nquads",
    "application/trig": "trig",
    "application/x-trig": "trig",
    "application/ld+json": "json-ld",
    "application/json": "json-ld",
    "text/n3": "n3",
}

# Content types too generic to say which serialization they carry: the extension
# of the URL is a better hint (a '.nq' file served as text/plain holds quads).
GENERIC_CONTENT_TYPE_FORMATS = {
    "text/plain": "nt",
}


def get_matching(resource) -> str:
    """Read and validate the 'matching' option of *resource*."""
    matching = resource.get("matching", EXACT_MATCHING).strip().upper()
    if matching not in VALID_MATCHINGS:
        raise ValueError(
            f"Option 'matching' of resource '{resource.name}' is '{matching}', "
            f"which is not valid. Must be one of: {sorted(VALID_MATCHINGS)}."
        )
    return matching


def is_absolute_iri(value: str) -> bool:
    """
    True when *value* is an absolute IRI that no prefixed name can be mistaken
    for: one with an authority (``http://...``) or an absolute path
    (``file:///...``), or a URN or tag IRI.
    """
    parts = urlsplit(value)
    return bool(parts.scheme) and (
        bool(parts.netloc)
        or parts.path.startswith("/")
        or parts.scheme.lower() in ("urn", "tag")
    )


def resolve_attributes(resource) -> tuple[str, ...]:
    """
    The 'attributes' option of *resource*, its prefixed names expanded with the
    well-known prefixes.
    """
    attributes = []
    for attribute in resource.get_list("attributes"):
        prefix, separator, local_name = attribute.partition(":")
        if is_absolute_iri(attribute):
            attributes.append(attribute)
        elif separator and prefix in WELL_KNOWN_PREFIXES:
            attributes.append(WELL_KNOWN_PREFIXES[prefix] + local_name)
        else:
            raise ValueError(
                f"Option 'attributes' of resource '{resource.name}' holds "
                f"'{attribute}', which is neither an absolute IRI nor a prefixed "
                "name with one of the prefixes "
                f"{', '.join(sorted(WELL_KNOWN_PREFIXES))}. Write it as a full IRI."
            )
    return tuple(attributes)


def guess_format(resource, url: str, content_type: str) -> str | None:
    """
    Determine the RDF serialization of a fetched vocabulary: the 'format'
    option wins, then the content type it was served with, then its extension.
    """
    declared = resource.get("format")
    if declared:
        return declared

    if content_type in CONTENT_TYPE_FORMATS:
        return CONTENT_TYPE_FORMATS[content_type]

    return (
        rdflib.util.guess_format(url.split("?")[0])
        or GENERIC_CONTENT_TYPE_FORMATS.get(content_type)
    )


def load_vocabulary_graph(resource) -> rdflib.Dataset:
    """
    Fetch and parse the SKOS vocabulary declared by *resource*.

    The vocabulary is parsed into a dataset whose default graph is the union of
    all its graphs, so that the triples of a vocabulary serialized as quads are
    not lost: a plain graph silently drops those in a named graph.
    """
    url = resource.get_url()
    if not url:
        raise ValueError(
            f"Resource '{resource.name}' does not declare the 'url' of the "
            "vocabulary to reconcile against."
        )
    if urlsplit(url).username is not None:
        raise ValueError(
            f"The 'url' of resource '{resource.name}' holds credentials. Keep "
            "them out of the URL: declare them as 'username' and 'password' in "
            f"the configuration file, in its '[RESOURCE:{resource.name}]' section."
        )

    response = fetch(
        url,
        username    = resource.get_username(),
        password    = resource.get_password(),
        accept      = ACCEPTED_MEDIA_TYPES,
        timeout     = resource.get_int("timeout", DEFAULT_TIMEOUT),
        description = "vocabulary",
    )

    rdf_format = guess_format(resource, url, response.content_type)

    graph = rdflib.Dataset(default_union=True)
    try:
        graph.parse(data=response.body, format=rdf_format)
    except Exception as exc:
        raise ValueError(
            f"The vocabulary of resource '{resource.name}' fetched from '{url}' "
            f"could not be parsed as RDF ({rdf_format or 'unknown format'}): "
            f"{exc}. Set the 'format' option of the resource to its "
            "serialization."
        ) from exc

    return graph


def build_index(resource, matched_attributes=((),)) -> ConceptIndex:
    """
    Build the concept index of a SKOS vocabulary resource.

    *matched_attributes* holds, for each execution of the function reconciling
    against the resource, the attributes it matches against: the ones it names,
    ``()`` when it names none, and ``None`` when it reads them from the data.
    An execution naming none matches against the 'attributes' option of the
    resource or, without it, against every property of the vocabulary whose
    value is a literal.

    Only those attributes are indexed, which keeps the shared context to the
    part of the vocabulary the mapping actually reconciles against. Concept
    schemes and collections are left out.
    """
    # The option only concerns the executions naming no attribute: a mistake in
    # it does not stop a mapping none of whose executions relies on it.
    default_attributes = ()
    if any(attributes == () for attributes in matched_attributes):
        default_attributes = resolve_attributes(resource)

    graph = load_vocabulary_graph(resource)

    attributes = {}
    every_attribute = False
    for execution_attributes in matched_attributes:
        if execution_attributes is None:
            every_attribute = True
        elif execution_attributes:
            attributes.update(dict.fromkeys(execution_attributes))
        elif default_attributes:
            attributes.update(dict.fromkeys(default_attributes))
        else:
            every_attribute = True

    index = ConceptIndex(matching=get_matching(resource))

    non_concepts = {
        subject
        for concept_class in NON_CONCEPT_CLASSES
        for subject in graph.subjects(RDF.type, concept_class)
    }

    literal_attributes = {}
    if every_attribute:
        # Iterating a dataset yields quads, its triples() the union of graphs.
        for concept, predicate, value in graph.triples((None, None, None)):
            if isinstance(value, rdflib.term.Literal) and concept not in non_concepts:
                index.add(str(predicate), value, str(concept))
                literal_attributes[str(predicate)] = None
    for attribute in attributes:
        predicate = rdflib.term.URIRef(attribute)
        for concept, value in graph.subject_objects(predicate):
            if concept not in non_concepts:
                index.add(attribute, value, str(concept))

    # An execution naming no attribute matches against these, not against every
    # indexed attribute: one only another execution names may take IRIs.
    index.default_attributes = default_attributes or tuple(literal_attributes)

    LOGGER.info(
        f"Resource '{resource.name}': indexed {len(index)} value(s) of "
        f"{len(index.entries)} attribute(s) of the vocabulary."
    )

    return index
