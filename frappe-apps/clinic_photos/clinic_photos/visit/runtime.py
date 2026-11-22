"""Side-effect-free visit feature activation predicate."""


def enabled_for_site(site, conf) -> bool:
    """Require an explicit boolean gate and exact server-configured site."""
    if not isinstance(site, str) or not site or site.strip() != site:
        return False
    if not callable(getattr(conf, "get", None)):
        return False
    configured = conf.get("clinic_visit_photos_site")
    return (
        conf.get("clinic_visit_photos_enabled") is True
        and isinstance(configured, str)
        and configured == site
    )
