"""Section 7 — dedup and vendor-name normalization/matching."""
from invoice_extraction.dedup import duplicate_key, file_hash, find_matching_vendor, normalize_vendor_name


class TestFileHash:
    def test_identical_content_hashes_identically(self) -> None:
        assert file_hash(b"same bytes") == file_hash(b"same bytes")

    def test_different_content_hashes_differently(self) -> None:
        assert file_hash(b"one") != file_hash(b"two")


class TestNormalizeVendorName:
    def test_case_and_whitespace_are_normalized(self) -> None:
        assert normalize_vendor_name("  ABC   Traders  ") == "abc traders"

    def test_common_corporate_suffixes_are_stripped(self) -> None:
        assert normalize_vendor_name("ABC Traders Pvt Ltd") == "abc traders"
        assert normalize_vendor_name("ABC Traders Private Limited") == "abc traders"
        assert normalize_vendor_name("ABC Traders (Pvt) Ltd") == "abc traders"

    def test_a_name_with_no_suffix_is_unaffected(self) -> None:
        assert normalize_vendor_name("Shopify Commerce Singapore") == "shopify commerce singapore"


class TestFindMatchingVendor:
    def test_an_exact_normalized_match_is_found(self) -> None:
        known = ["ABC Traders Pvt Ltd", "XYZ Suppliers"]
        assert find_matching_vendor("abc traders", known) == "ABC Traders Pvt Ltd"

    def test_a_close_but_not_identical_spelling_is_still_matched(self) -> None:
        """The whole point of fuzzy matching — an OCR-introduced typo in an
        otherwise-known vendor name should still resolve."""
        known = ["ABC Traders Pvt Ltd"]
        assert find_matching_vendor("ABC Tradres", known) == "ABC Traders Pvt Ltd"

    def test_an_unrelated_name_is_not_force_matched(self) -> None:
        known = ["ABC Traders Pvt Ltd"]
        assert find_matching_vendor("Totally Different Company", known) is None

    def test_an_empty_known_list_returns_none(self) -> None:
        assert find_matching_vendor("Anything", []) is None


class TestDuplicateKey:
    def test_same_inputs_produce_the_same_key(self) -> None:
        a = duplicate_key("ABC Traders Pvt Ltd", "INV-001", 1000.0, "2026-08-04")
        b = duplicate_key("abc traders", "inv-001", 1000.004, "2026-08-04")
        assert a == b

    def test_a_different_total_produces_a_different_key(self) -> None:
        a = duplicate_key("ABC Traders", "INV-001", 1000.0, "2026-08-04")
        b = duplicate_key("ABC Traders", "INV-001", 2000.0, "2026-08-04")
        assert a != b
