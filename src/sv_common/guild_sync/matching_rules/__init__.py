"""
Matching rules package.

Rules registry is now empty. Character ownership is established via
Battle.net OAuth (link_source='battlenet_oauth') during onboarding.
Manual character adds are done through the Settings → Characters form.
"""


def get_registered_rules() -> list:
    """Return all registered matching rules. Currently empty — OAuth provides ownership."""
    return []
