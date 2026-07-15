__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
Stage 4 — Join
==============
Merges child and parent DataFrames on one or more join conditions.

Public API
----------
merge_data(child, parent, join_conditions) -> pd.DataFrame
"""

import pandas as pd

from ...mapping.model import JoinCondition
from .references import join_pairs


def merge_data(
    child: pd.DataFrame,
    parent: pd.DataFrame,
    join_conditions: list[JoinCondition],
) -> pd.DataFrame:
    """
    Inner-join *child* and *parent* DataFrames on *join_conditions*.
    Parent columns are prefixed with ``parent_`` to avoid name collisions.
    Uses index-join when there is exactly one condition (faster path).
    """
    parent = parent.add_prefix("parent_")
    child_refs, parent_refs = join_pairs(join_conditions)
    parent_refs_pf = ["parent_" + r for r in parent_refs]

    if len(child_refs) == 1:
        child  = child.set_index(child_refs, drop=False)
        parent = parent.set_index(parent_refs_pf, drop=False)
        return child.join(parent, how="inner")

    return child.merge(parent, how="inner",
                       left_on=child_refs, right_on=parent_refs_pf)
