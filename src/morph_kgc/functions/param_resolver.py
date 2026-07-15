from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
Parameter Resolver
==================
Converts FNMLExecution.inputs into the concrete per-row parameter arrays
that the function callable expects.

This was previously an inline block inside execute_fnml().  Extracting it
here makes the execution step read top-to-bottom and allows independent
unit-testing of the parameter-preparation logic.

Public API
----------
resolve_params(data, execution, config) -> dict[str, list]
  Returns {function_param_name: [value_per_row, ...], ...}
"""

import pandas as pd

from ..constants import RML_CONSTANT, RML_TEMPLATE, RML_REFERENCE, RML_EXECUTION
from ..utils import get_references_in_template
from .model import FNMLExecution, InputBinding, ValueBinding


def _expand_template(data: pd.DataFrame, template: str) -> list:
    """Resolve an rml:template against *data* rows, return list of strings."""
    references = get_references_in_template(template)
    template   = template.replace("\\{", "{").replace("\\}", "}")

    result = pd.Series([""] * len(data), index=data.index)
    for ref in references:
        parts  = template.split("{" + ref + "}")
        result = result + parts[0] + data[ref].astype(str)
        template = ("{" + ref + "}").join(parts[1:])
    if template:
        result = result + template
    return list(result)


def _resolve_value_binding(
    data: pd.DataFrame,
    vb: ValueBinding,
) -> list:
    """Turn one ValueBinding into a per-row value list (non-EXECUTION types)."""
    if vb.map_type == RML_CONSTANT:
        return [vb.map_value] * len(data)
    if vb.map_type == RML_TEMPLATE:
        return _expand_template(data, vb.map_value)
    # RML_REFERENCE (or fallback)
    return list(data[vb.map_value])


def resolve_params(
    data: pd.DataFrame,
    execution: FNMLExecution,
    config,
    decorator_params: dict[str, str],
) -> dict[str, list]:
    """
    Build the concrete parameter dict expected by the function callable.

    decorator_params maps function_kwarg_name -> parameter_IRI
    (sourced from the @bif / @udf decorator).

    For each kwarg the resolver:
      1. Finds the matching InputBinding by parameter_IRI.
      2. Resolves each ValueBinding to a per-row list.
      3. If there are multiple ValueBindings (array parameter), wraps
         the per-row values into a list per row.
    """
    # Build a fast lookup: parameter_IRI -> InputBinding
    param_lookup: dict[str, InputBinding] = {
        ib.parameter_iri: ib for ib in execution.inputs
    }

    result: dict[str, list] = {}

    for kwarg_name, param_iri in decorator_params.items():
        if param_iri not in param_lookup:
            # parameter not provided — skip (function will use its default)
            continue

        ib = param_lookup[param_iri]
        # Filter out EXECUTION bindings — those were already evaluated
        # recursively before this call and the result lives in data columns.
        non_exec = [vb for vb in ib.values if vb.map_type != RML_EXECUTION]
        exec_vbs = [vb for vb in ib.values if vb.map_type == RML_EXECUTION]

        if exec_vbs:
            # Execution result is already a column in data
            per_row = [list(data[vb.map_value]) for vb in exec_vbs]
            per_row += [_resolve_value_binding(data, vb) for vb in non_exec]
        else:
            per_row = [_resolve_value_binding(data, vb) for vb in non_exec]

        if len(per_row) == 1:
            result[kwarg_name] = per_row[0]
        else:
            # array parameter: zip across the multiple value lists
            result[kwarg_name] = [
                list(vals) if len(vals) > 1 else vals[0]
                for vals in zip(*per_row)
            ]

    return result
