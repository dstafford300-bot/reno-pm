def sort_units_contingency_last(units: list[dict]) -> list[dict]:
    """Keep units in their existing (e.g. alphabetical) order, but always
    push anything named/containing 'Contingency' to the end."""
    return sorted(units, key=lambda u: "contingency" in u["unit_name"].lower())
