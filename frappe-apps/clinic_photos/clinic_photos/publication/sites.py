"""Explicit publication-site allowlist shared by the save, queue and child seams.

The disposable lab site keeps its historical unconditional allowance. One
additional site is allowed only when the server site configuration names that
exact site under ``clinic_photos_additional_synthetic_site``: an absent, mistyped,
differently cased, list-shaped or otherwise different value keeps the site denied,
and no request field, header or argv value can add a site.

This module is pure standard library. Importing it performs no Frappe import, no
site lookup and no filesystem, queue or database activity.
"""

LAB_SITE = "clinic-photo-accept.local"
ADDITIONAL_SITE = "marley.local"
ADDITIONAL_SITE_CONFIG_KEY = "clinic_photos_additional_synthetic_site"


def candidate_sites() -> frozenset:
    """Static superset for the pre-bootstrap argv and identity guards.

    Being a candidate is not permission: the loaded site configuration decides
    through :func:`is_allowed`.
    """
    return frozenset({LAB_SITE, ADDITIONAL_SITE})


def configured_additional_site(conf):
    """The explicitly configured additional site, or ``None``."""
    getter = getattr(conf, "get", None)
    if not callable(getter):
        return None
    try:
        value = getter(ADDITIONAL_SITE_CONFIG_KEY)
    except Exception:
        return None
    if isinstance(value, str) and value == ADDITIONAL_SITE:
        return ADDITIONAL_SITE
    return None


def is_allowed(site, conf) -> bool:
    """Exactly the lab site, or exactly the additionally configured site."""
    if not isinstance(site, str):
        return False
    if site == LAB_SITE:
        return True
    return site == configured_additional_site(conf)
