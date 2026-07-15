__author__ = "Julian Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
RML 1.2 constants for the materializer pipeline.

RML-star names replaced by their RML 1.2 equivalents (per attached paper):
    rml:quotedTriplesMap -> rml:tripleTermMap  (predicate on object map node)
    rml:RDFstarTriple    -> rml:TripleTerm     (rml:termType value)
    rml:StarMap          -> rml:TripleTermMap  (rdf:type of the object map node)

Legacy URIs (semweb.mmlab.be) are translated only in formats/rml.py and are
never used inside the materializer pipeline.
"""

from ..constants import RML_NAMESPACE

# ── RML 1.2 triple-term map ───────────────────────────────────────────────────
# Predicate on an object-map node declaring it as a triple-term map
RML_TRIPLE_TERM_MAP_PROPERTY = f"{RML_NAMESPACE}tripleTermMap"
# Class that types the object-map node
RML_TRIPLE_TERM_MAP_CLASS    = f"{RML_NAMESPACE}TripleTermMap"
# rml:termType value for a triple term
RML_TRIPLE_TERM_TYPE         = f"{RML_NAMESPACE}TripleTerm"

# Sentinel stored on TermMap.map_type after parsing so pipeline branches can
# detect a triple-term object map with a single equality check.
RML_TRIPLE_TERM_MAP = RML_TRIPLE_TERM_MAP_PROPERTY

# ── Asserted / non-asserted (RML 1.2 §2 / §3.3) ──────────────────────────────
# AssertedTriplesMap: triples are written to the output AND can be triple terms
RML_ASSERTED_TRIPLES_MAP_CLASS     = f"{RML_NAMESPACE}AssertedTriplesMap"
# NonAssertedTriplesMap: triples are used ONLY as triple terms (via rdf:reifies)
# and MUST NOT appear as standalone triples in the output dataset.
RML_NON_ASSERTED_TRIPLES_MAP_CLASS = f"{RML_NAMESPACE}NonAssertedTriplesMap"

# ── Direction map (RML 1.2 §2.3) ─────────────────────────────────────────────
RML_DIRECTION_MAP      = f"{RML_NAMESPACE}directionMap"
RML_DIRECTION_SHORTCUT = f"{RML_NAMESPACE}direction"  # shortcut; expanded by normalizer

# ── reifyingMap shortcut (RML 1.2 §3.1) ──────────────────────────────────────
# Expanded by normalizer.expand_shortcuts() before the pipeline sees the graph.
RML_REIFYING_MAP = f"{RML_NAMESPACE}reifyingMap"
