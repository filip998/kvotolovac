from __future__ import annotations


def parse_csv_query_values(value: str | None) -> list[str] | None:
    if not value:
        return None

    values = [item.strip() for item in value.split(",") if item.strip()]
    return values or None
