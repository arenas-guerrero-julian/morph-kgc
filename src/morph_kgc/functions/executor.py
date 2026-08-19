from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
FNML Executor
=============
Evaluates one rml:functionExecution against a source DataFrame and writes
the result into a new column named after the execution id.

Replaces the monolithic execute_fnml() that accepted a raw fnml_df DataFrame.
The new signature accepts either:
  - An FNMLExecution dataclass (preferred, from the new parser), OR
  - A legacy fnml_df DataFrame + execution_id string (bridge for callers that
    have not yet been migrated).

Public API
----------
execute_fnml(data, execution_or_df, execution_id_or_none, config,
             in_recursion=False) -> pd.DataFrame
"""

import pandas as pd

from ..constants import RML_EXECUTION
from ..utils import remove_null_values_from_dataframe

from .model         import FNMLExecution, InputBinding, ValueBinding
from .registry      import FunctionRegistry
from .param_resolver import resolve_params


# ── legacy bridge ─────────────────────────────────────────────────────────────

def _fnml_df_to_execution(
    fnml_df: pd.DataFrame,
    execution_id: str,
    execution_registry: dict[str, FNMLExecution] | None = None,
) -> FNMLExecution:
    """
    Convert the legacy fnml_df slice for *execution_id* into an FNMLExecution.
    Called only by the bridge path; the new parser produces FNMLExecution directly.
    """
    rows = fnml_df[fnml_df["function_execution"] == execution_id]
    if rows.empty:
        raise KeyError(f"Execution {execution_id!r} not found in fnml_df.")

    function_iri = rows.iloc[0]["function_map_value"]

    # Group rows by parameter_map_value to build InputBinding list
    inputs: dict[str, InputBinding] = {}
    for _, row in rows.iterrows():
        param = row["parameter_map_value"]
        if pd.isna(param):
            continue
        if param not in inputs:
            inputs[param] = InputBinding(parameter_iri=param)
        nested_execution = None

        if row["value_map_type"] == RML_EXECUTION:
            if execution_registry is None:
                raise ValueError(
                    "An execution registry is required for nested FNML functions."
                )

            nested_execution = execution_registry.get(
                row["value_map_value"]
            )

            if nested_execution is None:
                raise KeyError(
                    f"Nested execution {row['value_map_value']!r} "
                    "was not found in the execution registry."
                )

        inputs[param].values.append(
            ValueBinding(
                map_type=row["value_map_type"],
                map_value=row["value_map_value"],
                nested_execution=nested_execution,
            )
        )

    return FNMLExecution(
        execution_id = execution_id,
        function_iri = function_iri,
        inputs       = list(inputs.values()),
    )


# ── core executor ─────────────────────────────────────────────────────────────

def _execute(
    data: pd.DataFrame,
    execution: FNMLExecution,
    config,
    in_recursion: bool,
) -> pd.DataFrame:
    """Internal: evaluate *execution* against *data* rows."""

    function, decorator_params = FunctionRegistry.get(execution.function_iri, config)

    # Recursively evaluate any nested function executions first so their
    # results are available as columns in data before resolving parameters.
    for ib in execution.inputs:
        for vb in ib.values:
            if vb.map_type != RML_EXECUTION:
                continue

            nested = vb.nested_execution

            if nested is None:
                raise KeyError(
                    f"Nested execution {vb.map_value!r} was not attached "
                    f"to execution {execution.execution_id!r}."
                )

            data = _execute(
                data,
                nested,
                config,
                in_recursion=True,
            )

    params     = resolve_params(data, execution, config, decorator_params)
    exec_res   = []
    for i in range(len(data)):
        row_params = {k: v[i] for k, v in params.items()}
        exec_res.append(function(**row_params))

    data[execution.execution_id] = exec_res

    data = remove_null_values_from_dataframe(
        data, config, execution.execution_id, column=execution.execution_id
    )

    if not in_recursion:
        # explode list results only at the outermost call
        data = data.explode(execution.execution_id)

    return data


# ── public API ────────────────────────────────────────────────────────────────

def execute_fnml(
    data: pd.DataFrame,
    execution_or_df,
    execution_id: str | None = None,
    config=None,
    in_recursion: bool = False,
    execution_registry: dict[str, FNMLExecution] | None = None,
) -> pd.DataFrame:
    """
    Execute an FNML function and write the result into data[execution_id].

    Accepts two calling conventions:

    New (preferred):
        execute_fnml(data, fnml_execution, config=config)
        where fnml_execution is an FNMLExecution dataclass.

    Legacy bridge (backward compat with stage_terms._apply_fnml):
        execute_fnml(data, fnml_df, execution_id, config)
        where fnml_df is the narrow DataFrame produced by _fnml_to_df().
    """
    if isinstance(execution_or_df, FNMLExecution):
        execution = execution_or_df
    else:
        # legacy: execution_or_df is a DataFrame, execution_id is a string
        if execution_id is None:
            raise ValueError(
                "execution_id must be provided when calling execute_fnml "
                "with a DataFrame (legacy bridge mode)."
            )
        execution = _fnml_df_to_execution(
            execution_or_df,
            execution_id,
            execution_registry,
        )

    return _execute(data, execution, config, in_recursion)
