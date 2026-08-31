from invoice_extraction.geography import detect_city, detect_country


class TestCityDetection:
    def test_a_known_pakistani_city_is_detected(self) -> None:
        assert detect_city("Shop #3, Al Waheed Arcade, Islamabad.") == "Islamabad"

    def test_a_known_indian_city_is_detected(self) -> None:
        assert detect_city("Ekta Nagar, Ghatkopar (W), Mumbai - 400 086.") == "Mumbai"

    def test_no_known_city_returns_none(self) -> None:
        assert detect_city("Some address with no recognizable city name") is None


class TestCountryDetection:
    def test_country_is_inferred_from_a_matched_city(self) -> None:
        assert detect_country("... Islamabad.", city="Islamabad") == "Pakistan"

    def test_country_is_inferred_from_a_phone_country_code_when_no_city_matched(self) -> None:
        assert detect_country("Call us: +92 51 84 82 452-53", city=None) == "Pakistan"

    def test_a_matched_city_takes_priority_over_a_phone_code(self) -> None:
        # A vendor's contact number's country code doesn't always match
        # where the shop physically is (e.g. a VOIP/toll-free number) — a
        # recognized city is stronger, more direct evidence.
        assert detect_country("Mumbai office, +1 800 555 0100", city="Mumbai") == "India"

    def test_no_evidence_at_all_returns_none_never_a_default(self) -> None:
        """The exact bug this module exists to avoid: never assume a
        country (e.g. USA) just because none was detected."""
        assert detect_country("Some text with no city or phone code", city=None) is None
