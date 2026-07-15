from __future__ import annotations

__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
FNML domain model
=================
Typed representation of a resolved FNML execution, replacing the narrow
DataFrame (fnml_df) that was previously threaded through every call.

FNMLExecution    — one function call: function IRI + list of InputBinding
InputBinding     — one rml:input: parameter IRI + list of ValueBinding
ValueBinding     — one rml:inputValueMap: map_type + map_value
"""

from dataclasses import dataclass, field


@dataclass(slots=True)
class ValueBinding:
    """Corresponds to one rml:inputValueMap node."""
    map_type:  str   # rml:constant | rml:template | rml:reference | rml:functionExecution
    map_value: str


@dataclass(slots=True)
class InputBinding:
    """Corresponds to one rml:input node."""
    parameter_iri: str            # rml:parameterMap constant value
    values:        list[ValueBinding] = field(default_factory=list)


@dataclass(slots=True)
class FNMLExecution:
    """
    Fully resolved representation of one rml:functionExecution node.
    Replaces the per-execution slice of the legacy fnml_df DataFrame.
    """
    execution_id: str             # blank-node or IRI identifying the execution
    function_iri: str             # rml:functionMap constant value
    inputs:       list[InputBinding] = field(default_factory=list)
