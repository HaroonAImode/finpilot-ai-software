"""Country/city detection — never defaults to a country when no evidence
exists (the same "no unsafe default" discipline as currency.py). Evidence
sources: a recognized city name actually present in the document text, or
an international phone country-code prefix. Deliberately a small, growable
table (same maintenance philosophy as labels.py's own docstring: extend it
from real documents seen, not upfront guessing) seeded with the cities this
project's real test documents actually contain.
"""
import re
from typing import Optional

# city (lowercase) -> country. Seeded from real documents this library has
# actually been tested against, not an attempt at exhaustive geography.
_CITY_COUNTRY: dict[str, str] = {
    "islamabad": "Pakistan", "lahore": "Pakistan", "karachi": "Pakistan", "rawalpindi": "Pakistan",
    "faisalabad": "Pakistan", "multan": "Pakistan", "peshawar": "Pakistan", "quetta": "Pakistan",
    "gujranwala": "Pakistan", "sialkot": "Pakistan", "hyderabad": "Pakistan",
    "mumbai": "India", "delhi": "India", "bangalore": "India", "chennai": "India", "kolkata": "India",
}

# International calling code -> country, used only when no city matched.
_COUNTRY_PHONE_CODES: list[tuple[str, str]] = [
    (r"\+92\b", "Pakistan"),
    (r"\+91\b", "India"),
    (r"\+44\b", "United Kingdom"),
    (r"\+1\b", "United States"),
]

_COMPILED_PHONE_CODES = [(re.compile(pattern), country) for pattern, country in _COUNTRY_PHONE_CODES]


def detect_city(text: str) -> Optional[str]:
    lowered = text.lower()
    for city in _CITY_COUNTRY:
        if re.search(rf"\b{city}\b", lowered):
            return city.title()
    return None


def detect_country(text: str, city: Optional[str] = None) -> Optional[str]:
    """`city` should be the result of detect_city(text) if already computed
    (avoids re-scanning) — a matched city is stronger evidence than a phone
    code alone (a vendor's contact number's country doesn't always match
    where the shop physically is, e.g. a toll-free/VOIP number), so it's
    checked first."""
    if city is not None:
        matched = _CITY_COUNTRY.get(city.lower())
        if matched:
            return matched
    for pattern, country in _COMPILED_PHONE_CODES:
        if pattern.search(text):
            return country
    return None
