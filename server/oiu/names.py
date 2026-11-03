"""Owner-chosen device names, cleaned for display."""


def specific(value):
    clean=''.join(c for c in (value or '') if c.isprintable()).strip()[:80]
    return clean if clean.casefold() not in ('','iphone','apple iphone','unknown') else None
