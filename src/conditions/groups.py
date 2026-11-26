"""
Group modes (CL-24C): how many of the children must be true
===========================================================
``all`` = every child, ``any`` = at least 1, ``any_1`` = exactly 1, ``any_2`` = at least 2
(owner decision 26 Nov).

A child can be *unknown* (not enough history yet). Unknown counts as "could be true or false".
The group answers True or False only when that cannot change the answer, otherwise it is
unknown too (and an unknown final answer is false). So ``any`` is true when one child is true even
if another is unknown, and ``all`` is not true while a child is unknown.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def combine(mode: str, children: list[pd.Series]) -> pd.Series:
    """The group's true/false/unknown answer per row ("boolean" dtype, NA = unknown)."""
    true_count = sum(child.fillna(False).to_numpy(dtype=bool).astype(int) for child in children)
    unknown_count = sum(child.isna().to_numpy(dtype=int) for child in children)
    fewest, most = true_count, true_count + unknown_count  # the true count is somewhere between

    if mode == "any_1":  # exactly one true
        yes = (fewest == 1) & (most == 1)
        no = (fewest > 1) | (most < 1)
    else:  # "at least k true"
        k = {"all": len(children), "any": 1, "any_2": 2}[mode]
        yes = fewest >= k
        no = most < k

    answer = np.full(len(yes), pd.NA, dtype=object)
    answer[yes] = True
    answer[no] = False
    return pd.Series(answer, index=children[0].index, dtype="boolean")
