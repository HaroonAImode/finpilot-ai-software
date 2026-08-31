"""Sender allow/deny matching and the needs-review decision — the filtering
quality system docs/email-connector-plan.md §5/§5b describes.

Motivated by real numbers: the first live sync imported 57 attachments, 34
of them images. Everything here exists to stop that.
"""
import pytest

from app.models import ReviewStatus, SenderRule, SenderRuleAction
from app.services.attachment_filter import (
    AUTO_IMPORT_CONFIDENCE, decide_review_status, matches_sender, sender_verdict,
)


def _rule(pattern: str, action: SenderRuleAction) -> SenderRule:
    return SenderRule(pattern=pattern, action=action)


class TestMatchesSender:
    def test_full_address_matches_exactly(self) -> None:
        assert matches_sender("billing@vendor.com", "billing@vendor.com") is True

    def test_full_address_does_not_match_a_different_mailbox_at_that_domain(self) -> None:
        assert matches_sender("billing@vendor.com", "newsletter@vendor.com") is False

    def test_bare_domain_matches_any_address_there(self) -> None:
        assert matches_sender("vendor.com", "billing@vendor.com") is True
        assert matches_sender("vendor.com", "ap@vendor.com") is True

    def test_matching_is_case_insensitive(self) -> None:
        assert matches_sender("Billing@Vendor.com", "BILLING@vendor.COM") is True
        assert matches_sender("VENDOR.com", "ap@vendor.com") is True

    def test_surrounding_whitespace_is_ignored(self) -> None:
        assert matches_sender("  vendor.com  ", " ap@vendor.com ") is True

    def test_a_domain_rule_does_not_match_a_lookalike_suffix(self) -> None:
        """The security case: "vendor.com" must not grant
        "vendor.com.evil.net", which a naive endswith() would."""
        assert matches_sender("vendor.com", "ap@vendor.com.evil.net") is False

    def test_a_domain_rule_does_not_match_a_subdomain(self) -> None:
        """Deliberate: allowing vendor.com should not silently allow
        anything anyone can stand up under it."""
        assert matches_sender("vendor.com", "ap@mail.vendor.com") is False

    def test_a_missing_sender_never_matches(self) -> None:
        assert matches_sender("vendor.com", None) is False
        assert matches_sender("vendor.com", "") is False

    def test_an_empty_pattern_never_matches(self) -> None:
        assert matches_sender("", "ap@vendor.com") is False
        assert matches_sender("   ", "ap@vendor.com") is False


class TestSenderVerdict:
    def test_no_rules_means_no_verdict(self) -> None:
        assert sender_verdict([], "ap@vendor.com") is None

    def test_an_allow_rule_wins_when_it_is_the_only_match(self) -> None:
        rules = [_rule("vendor.com", SenderRuleAction.allow)]
        assert sender_verdict(rules, "ap@vendor.com") is SenderRuleAction.allow

    def test_a_deny_rule_wins_when_it_is_the_only_match(self) -> None:
        rules = [_rule("newsletter@vendor.com", SenderRuleAction.deny)]
        assert sender_verdict(rules, "newsletter@vendor.com") is SenderRuleAction.deny

    def test_deny_beats_allow_when_both_match(self) -> None:
        """The realistic conflict: allow the whole domain, deny one noisy
        mailbox inside it. Collecting less is the safe resolution."""
        rules = [
            _rule("vendor.com", SenderRuleAction.allow),
            _rule("newsletter@vendor.com", SenderRuleAction.deny),
        ]
        assert sender_verdict(rules, "newsletter@vendor.com") is SenderRuleAction.deny
        # …and the rest of the domain is still allowed.
        assert sender_verdict(rules, "billing@vendor.com") is SenderRuleAction.allow

    def test_rules_for_other_senders_are_ignored(self) -> None:
        rules = [_rule("other.com", SenderRuleAction.deny)]
        assert sender_verdict(rules, "ap@vendor.com") is None


class TestDecideReviewStatus:
    def test_a_denied_sender_is_dropped_entirely(self) -> None:
        """None means "do not even store a row" — keeping one would keep a
        record of who emails this mailbox, which is what a deny rule asks
        us not to do."""
        rules = [_rule("newsletter@vendor.com", SenderRuleAction.deny)]
        status, reason = decide_review_status(rules, "newsletter@vendor.com", confidence=0.95)
        assert status is None
        assert reason == "denied_sender"

    def test_a_denied_sender_is_dropped_even_at_high_confidence(self) -> None:
        """An explicit human rule outranks the classifier."""
        rules = [_rule("vendor.com", SenderRuleAction.deny)]
        status, _ = decide_review_status(rules, "billing@vendor.com", confidence=1.0)
        assert status is None

    def test_an_allowed_sender_is_imported_even_at_low_confidence(self) -> None:
        """The other half of the same principle: an explicit allow means
        import it whether or not the filename looks financial."""
        rules = [_rule("vendor.com", SenderRuleAction.allow)]
        status, reason = decide_review_status(rules, "billing@vendor.com", confidence=0.0)
        assert status is ReviewStatus.imported
        assert reason == "allowed_sender"

    def test_high_confidence_with_no_rules_is_imported(self) -> None:
        status, reason = decide_review_status([], "someone@unknown.com", confidence=0.95)
        assert status is ReviewStatus.imported
        assert reason is None

    def test_exactly_at_the_threshold_is_imported(self) -> None:
        status, _ = decide_review_status([], "someone@unknown.com", confidence=AUTO_IMPORT_CONFIDENCE)
        assert status is ReviewStatus.imported

    def test_low_confidence_with_no_rules_goes_to_review(self) -> None:
        """The case that fixes "34 of 57 imported files were images": an
        unrecognised attachment from an unknown sender is offered, not taken."""
        status, reason = decide_review_status([], "someone@unknown.com", confidence=0.0)
        assert status is ReviewStatus.needs_review
        assert reason == "low_confidence"

    def test_a_missing_sender_falls_back_to_confidence_alone(self) -> None:
        rules = [_rule("vendor.com", SenderRuleAction.allow)]
        assert decide_review_status(rules, None, confidence=0.95)[0] is ReviewStatus.imported
        assert decide_review_status(rules, None, confidence=0.1)[0] is ReviewStatus.needs_review
