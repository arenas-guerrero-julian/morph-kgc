__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
Stage 3 — Term Serialisation
=============================
Converts raw source values in a DataFrame into N-Triples-serialized RDF terms
(IRIs, blank nodes, typed literals, language-tagged strings).

Handles all term-map types: rml:template, rml:constant, rml:reference,
rml:execution (FNML), and — new in RML 1.2 — directional language-tagged strings.

Public API
----------
materialize_terms(data, rule, rml_mapping, config, columns_alias='') -> pd.DataFrame
"""

import pandas as pd

from falcon.uri import encode_value
from urllib.parse import quote

from ...constants import (
    RML_TEMPLATE, RML_CONSTANT, RML_REFERENCE, RML_EXECUTION,
    RML_PARENT_TRIPLES_MAP, RML_REIFYING_MAP,
    RML_IRI, RML_LITERAL, RML_BLANK_NODE,
    RML_LANGUAGE_MAP, RML_DATATYPE_MAP,
    XSD_BOOLEAN, XSD_DATETIME, XSD_INTEGER,
)
from ...mapping.model import RMLMapping, RMLRule, FNMLRule
from ...utils import get_references_in_template
from ...functions import execute_fnml


# ── FNML bridge ───────────────────────────────────────────────────────────────

def _fnml_to_df(fnml_rules: list[FNMLRule]) -> pd.DataFrame:
    """Build the narrow DataFrame that execute_fnml() expects, on demand."""
    return pd.DataFrame([
        {
            "function_execution":  e.function_execution,
            "function_map_value":  e.function_map_value,
            "parameter_map_value": e.parameter_map_value,
            "value_map_type":      e.value_map_type,
            "value_map_value":     e.value_map_value,
        }
        for e in fnml_rules
    ])


# ── Literal escaping ──────────────────────────────────────────────────────────

def _escape_literal(series: pd.Series, datatype: str, config) -> pd.Series:
    if datatype == XSD_BOOLEAN:
        series = series.str.lower()
    elif datatype == XSD_DATETIME:
        series = series.str.replace(" ", "T", regex=False)
    elif datatype == XSD_INTEGER:
        series = series.astype(float).astype(int).astype(str)
    series = (
        series
        .str.replace("\\", "\\\\", regex=False)
        .str.replace("\n", "\\n",   regex=False)
        .str.replace("\r", "\\r",   regex=False)
        .str.replace('"',  '\\"',   regex=False)
    )
    for char in config.literal_escaping_chars:
        if char not in ('"', "\n", "\\", "\r"):
            esc = f"\\{char}" if char in ("\n", "\r", "\t", "\b", "\f") else f"\\\\{char}"
            series = series.str.replace(char, esc, regex=False)
    return series


# ── Core term materialisation ─────────────────────────────────────────────────

def _apply_template(
    data: pd.DataFrame,
    template: str,
    expression_type: str,
    config,
    position: str,
    columns_alias: str = "",
    termtype: str = "",
    datatype: str = "",
) -> pd.DataFrame:
    """Write serialized RDF term into data[position] for template/reference/constant maps."""
    if expression_type == RML_REFERENCE:
        template = f"{{{template}}}"

    references = get_references_in_template(template)
    template = template.replace("\\{", "{").replace("\\}", "}")
    data[position] = ""

    for ref in references:
        data["_ref"] = data[columns_alias + ref]

        if termtype.strip() == RML_IRI and expression_type == RML_TEMPLATE:
            safe = config.safe_percent_encoding
            if safe:
                data["_ref"] = data["_ref"].apply(lambda x: quote(x, safe=safe))
            else:
                data["_ref"] = data["_ref"].apply(encode_value)
        elif termtype.strip() == RML_LITERAL:
            data["_ref"] = _escape_literal(data["_ref"], datatype, config)

        parts = template.split("{" + ref + "}")
        data[position] = data[position] + parts[0] + data["_ref"]
        template = ("{" + ref + "}").join(parts[1:])

    if template:
        data[position] = data[position] + template

    t = termtype.strip()
    if t == RML_IRI:
        data[position] = "<" + data[position] + ">"
    elif t == RML_BLANK_NODE:
        data[position] = "_:" + data[position]
    elif t == RML_LITERAL:
        data[position] = '"' + data[position] + '"'

    return data


def _apply_parent_triples_map(
    data: pd.DataFrame,
    tm,                            # TermMap with map_type == RML_PARENT_TRIPLES_MAP
    rml_mapping: RMLMapping,
    config,
    position: str,
    columns_alias: str = "",
    datatype: str = "",
) -> pd.DataFrame:
    """
    Resolve a referencing object map: materialize the parent triples map's
    subject term directly against *data*, which — when a join condition is
    present — has already been merged with the parent's raw columns
    (prefixed by *columns_alias*, e.g. "parent_") in materialize_rule().
    No additional loading or merging happens here.
    """
    parent_rule = rml_mapping.get_rule(tm.map_value)

    return _write_term(
        data, parent_rule.subject, position, rml_mapping, config,
        columns_alias=columns_alias,
        termtype_override=parent_rule.subject.term_type,
        datatype=datatype,
    )


def _apply_fnml(
    data: pd.DataFrame,
    fnml_execution: str,
    rml_mapping: RMLMapping,
    config,
    position: str,
    termtype: str = RML_LITERAL,
    datatype: str = "",
) -> pd.DataFrame:
    """Write serialised RDF term into data[position] for FNML execution maps."""
    fnml_df = _fnml_to_df(rml_mapping.fnml_rules)
    data = execute_fnml(data, fnml_df, fnml_execution, config)
    data[fnml_execution] = data[fnml_execution].astype(str)

    t = termtype.strip()
    if t == RML_LITERAL:
        data[fnml_execution] = _escape_literal(data[fnml_execution], datatype, config)
        data[position] = '"' + data[fnml_execution] + '"'
    elif t == RML_IRI:
        data[fnml_execution] = data[fnml_execution].apply(str.strip)
        data[position] = "<" + data[fnml_execution] + ">"
    elif t == RML_BLANK_NODE:
        data[position] = "_:" + data[fnml_execution]

    return data


def _write_term(
    data: pd.DataFrame,
    tm,                           # TermMap | None
    position: str,
    rml_mapping: RMLMapping,
    config,
    columns_alias: str = "",
    termtype_override: str | None = None,
    datatype: str = "",
) -> pd.DataFrame:
    """Dispatch to _apply_template, _apply_fnml, or _apply_parent_triples_map
    based on tm.map_type."""
    if tm is None:
        return data
    termtype = termtype_override if termtype_override is not None else (tm.term_type or "")
    if tm.map_type in (RML_TEMPLATE, RML_CONSTANT, RML_REFERENCE):
        return _apply_template(
            data, tm.map_value, tm.map_type, config, position,
            columns_alias=columns_alias, termtype=termtype, datatype=datatype,
        )
    elif tm.map_type == RML_PARENT_TRIPLES_MAP:
        return _apply_parent_triples_map(
            data, tm, rml_mapping, config, position,
            columns_alias=columns_alias, datatype=datatype,
        )
    elif tm.map_type == RML_EXECUTION:
        return _apply_fnml(
            data, tm.map_value, rml_mapping, config, position,
            termtype=termtype, datatype=datatype,
        )
    return data


# ── Public stage entry point ──────────────────────────────────────────────────

def materialize_terms(
    data: pd.DataFrame,
    rule: RMLRule,
    rml_mapping: RMLMapping,
    config,
    columns_alias: str = "",
) -> pd.DataFrame:
    """
    Materialize subject / predicate / object / graph terms for every row in
    *data* and return the DataFrame with those columns populated.
    """
    om = rule.object_

    # subject
    data = _write_term(data, rule.subject, "subject", rml_mapping, config)
    # predicate
    data = _write_term(data, rule.predicate, "predicate", rml_mapping, config,
                       termtype_override=RML_IRI)

    # object
    data = _write_term(
        data, om, "object", rml_mapping, config,
        columns_alias=columns_alias,
        datatype=om.lang_datatype_map_value or "" if om else "",
    )

    # language / datatype annotation
    if om is not None:
        if om.lang_datatype == RML_LANGUAGE_MAP:
            if om.lang_datatype_map_type in (RML_TEMPLATE, RML_CONSTANT, RML_REFERENCE):
                data = _apply_template(
                    data, om.lang_datatype_map_value, om.lang_datatype_map_type,
                    config, "_lang",
                )
            elif om.lang_datatype_map_type == RML_EXECUTION:
                data = _apply_fnml(
                    data, om.lang_datatype_map_value, rml_mapping, config, "_lang",
                )
            # RML 1.2: directional language-tagged string
            if om.direction_map_value:
                data["object"] = (
                    data["object"] + "@" + data["_lang"] + "--" + om.direction_map_value
                )
            else:
                data["object"] = data["object"] + "@" + data["_lang"]

        elif om.lang_datatype == RML_DATATYPE_MAP:
            if om.lang_datatype_map_type in (RML_TEMPLATE, RML_CONSTANT, RML_REFERENCE):
                data = _apply_template(
                    data, om.lang_datatype_map_value, om.lang_datatype_map_type,
                    config, "_dtype", termtype=RML_IRI,
                )
            elif om.lang_datatype_map_type == RML_EXECUTION:
                data = _apply_fnml(
                    data, om.lang_datatype_map_value, rml_mapping, config, "_dtype",
                    termtype=RML_IRI,
                )
            data["object"] = data["object"] + "^^" + data["_dtype"]

    return data
