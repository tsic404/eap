"""The one case-folding operator (C-122): Unicode default full lowercase mapping.

``str.lower()`` *is* that mapping — full (not simple) lowercase, including
SpecialCasing's unconditional entries and the conditional ``Final_Sigma`` rule
evaluated over the whole string, with no locale tailoring. The two nearby
operators are different algorithms and must not be used for comparison:
``str.casefold()`` (``ß`` → ``ss``) and SQL ``lower()`` (whose result follows
the database collation). Folded values are therefore compared on this
operator's output on both sides; the stored key columns are pinned to
``COLLATE "C"`` so the database's equality test on them is code-point equality
as well.
"""

from __future__ import annotations


def fold(value: str) -> str:
    """Return the C-122 fold of ``value``; equality of folds is equality of domains."""
    return value.lower()


def fold_email_domain(email: str) -> str:
    """Fold the domain part of ``email`` — after its last ``@`` (C-122 ①)."""
    return fold(email.rsplit("@", 1)[-1])
