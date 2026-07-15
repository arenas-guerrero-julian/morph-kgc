__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
Stage 2 — Reference Collection
===============================
Determines which source columns are needed to materialize a rule, including
join-condition columns and recursively nested FNML / quoted-triple maps.

Public API
----------
collect_references(rule, rml_mapping, only_subject_map=False) -> set[str]
join_pairs(join_conditions) -> (list[str], list[str])
"""
    
from ...constants import (
    RML_TEMPLATE, RML_REFERENCE, RML_EXECUTION,
    RML_TRIPLE_TERM_MAP, RML_PARENT_TRIPLES_MAP,
)
from ...mapping.model import RMLMapping, RMLRule, TermMap, JoinCondition
from ...utils import get_references_in_template


def join_pairs(join_conditions: list[JoinCondition]) -> tuple[list[str], list[str]]:
    """Unpack a list of JoinCondition into (child_refs, parent_refs)."""
    return (
        [jc.child_value for jc in join_conditions],
        [jc.parent_value for jc in join_conditions],
    )


def _refs_from_fnml(rml_mapping: RMLMapping, execution_id: str) -> list[str]:
    refs = []
    for entry in rml_mapping.fnml_rules:
        if entry.function_execution != execution_id:
            continue
        if entry.value_map_type == RML_TEMPLATE:
            refs.extend(get_references_in_template(entry.value_map_value))
        elif entry.value_map_type == RML_REFERENCE:
            refs.append(entry.value_map_value)
        elif entry.value_map_type == RML_EXECUTION:
            refs.extend(_refs_from_fnml(rml_mapping, entry.value_map_value))
    return refs


def _refs_from_term_map(tm: TermMap | None, rml_mapping: RMLMapping) -> list[str]:
    if tm is None:
        return []
    if tm.map_type == RML_TEMPLATE:
        return get_references_in_template(tm.map_value)
    if tm.map_type == RML_REFERENCE:
        return [tm.map_value]
    if tm.map_type == RML_EXECUTION:
        return _refs_from_fnml(rml_mapping, tm.map_value)
    return []


def collect_references(
    rule: RMLRule,
    rml_mapping: RMLMapping,
    only_subject_map: bool = False,
) -> set[str]:
    """Return all source-column references needed to materialize *rule*."""
    refs: list[str] = []

    term_maps = (
        [rule.subject]
        if only_subject_map
        else [rule.subject, rule.predicate, rule.object_, rule.graph]
    )
    for tm in term_maps:
        refs.extend(_refs_from_term_map(tm, rml_mapping))

    if not only_subject_map and rule.object_ is not None:
        om = rule.object_
        if om.lang_datatype_map_type == RML_TEMPLATE and om.lang_datatype_map_value:
            refs.extend(get_references_in_template(om.lang_datatype_map_value))
        elif om.lang_datatype_map_type == RML_REFERENCE and om.lang_datatype_map_value:
            refs.append(om.lang_datatype_map_value)
        elif om.lang_datatype_map_type == RML_EXECUTION and om.lang_datatype_map_value:
            refs.extend(_refs_from_fnml(rml_mapping, om.lang_datatype_map_value))

    if not only_subject_map and rule.object_ is not None:
        om = rule.object_

        if om.map_type == RML_TRIPLE_TERM_MAP:
            base_rule = rml_mapping.get_rule(om.map_value)  # rml:tripleTermMap target
            refs.extend(collect_references(base_rule, rml_mapping))  # full pattern, not subject-only
            if om.join_conditions:
                child_refs, _ = join_pairs(om.join_conditions)
                refs.extend(child_refs)

        elif om.map_type == RML_PARENT_TRIPLES_MAP:
            parent_rule = rml_mapping.get_rule(om.map_value)
            if om.join_conditions:
                # only need the join child-side columns here; parent-side
                # columns are collected separately when loading parent_data
                child_refs, _ = join_pairs(om.join_conditions)
                refs.extend(child_refs)
            else:
                # same logical source, row-aligned: parent's subject-map
                # references must be present in THIS rule's own data
                refs.extend(collect_references(parent_rule, rml_mapping, only_subject_map=True))

    return set(refs)
