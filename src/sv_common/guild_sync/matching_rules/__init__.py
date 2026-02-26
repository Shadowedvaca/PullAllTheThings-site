"""
Matching rules package.

Rules are executed in order (lowest order first) by the runner.
Adding a new matching pattern means:
  1. Create a new rule class in a new file
  2. Import it here and add it to get_registered_rules()
"""

from .name_match_rule import NameMatchRule
from .note_group_rule import NoteGroupRule


def get_registered_rules() -> list:
    """Return all matching rules sorted by execution order."""
    return sorted(
        [
            NoteGroupRule(),
            NameMatchRule(),
        ],
        key=lambda r: r.order,
    )
