"""Layered attachment filter — docs/email-connector-plan.md §5.

Two cheap checks (size, extension) run before anything is downloaded, since
a mailbox has thousands of attachments and most are not financial documents
(signature images, tracking pixels, newsletters, calendar invites). The
provider-side query (has:attachment + a date floor) already did the
cheapest filtering — this is what's left once Gmail has handed back a
plausible candidate list.

Sender allow/deny rules and the needs-review decision live here too — see
§5b for the decision flow and why an unapproved attachment is never
downloaded.
"""
from app.models import ReviewStatus, SenderRule, SenderRuleAction

MIN_ATTACHMENT_SIZE_BYTES = 20 * 1024  # signature images and tracking pixels are almost always smaller

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "xlsx", "xls", "csv", "docx", "doc"}
DENIED_EXTENSIONS = {"ics", "vcf", "p7s", "asc", "gif"}

#: Categorisation confidence at or above which an attachment is imported
#: without asking. Matches rules.yaml's own minimum_confidence_threshold —
#: below it the classifier is effectively saying "I don't recognise this".
AUTO_IMPORT_CONFIDENCE = 0.70


def should_import(filename: str, size: int) -> tuple[bool, str | None]:
    """Returns (should_import, reason_skipped). reason_skipped is None when
    should_import is True, and a short machine-readable reason otherwise —
    useful for a future "why wasn't this imported" surface without needing
    to store every skipped attachment just to explain a decision."""
    if size < MIN_ATTACHMENT_SIZE_BYTES:
        return False, "too_small"

    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension in DENIED_EXTENSIONS:
        return False, "denied_extension"
    if extension not in ALLOWED_EXTENSIONS:
        return False, "extension_not_allowed"

    return True, None


def matches_sender(pattern: str, from_address: str | None) -> bool:
    """Whether a rule pattern matches a sender address.

    A pattern with an "@" is a full address and must match exactly. Without
    one it is a bare domain and matches any address at that domain —
    "vendor.com" covers billing@vendor.com and ap@vendor.com alike, which is
    how people actually think about "import everything from this company".
    Subdomains are deliberately NOT matched: "vendor.com" should not silently
    grant "phish.vendor.com.evil.net", and exact-suffix matching on the
    domain part is what keeps that honest.
    """
    if not from_address:
        return False
    address = from_address.strip().lower()
    pattern = pattern.strip().lower()
    if not pattern:
        return False
    if "@" in pattern:
        return address == pattern
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    return domain == pattern


def sender_verdict(rules: list[SenderRule], from_address: str | None) -> SenderRuleAction | None:
    """The winning rule action for this sender, or None if nothing matches.

    Deny wins over allow when both match. A rule conflict is a mistake
    somewhere, and the safe way to resolve it is to collect less rather than
    more — the same instinct behind not downloading needs-review items.
    """
    matched = [rule.action for rule in rules if matches_sender(rule.pattern, from_address)]
    if SenderRuleAction.deny in matched:
        return SenderRuleAction.deny
    if SenderRuleAction.allow in matched:
        return SenderRuleAction.allow
    return None


def decide_review_status(
    rules: list[SenderRule], from_address: str | None, confidence: float,
) -> tuple[ReviewStatus | None, str | None]:
    """Decide what happens to an attachment that already passed size/extension.

    Returns (status, reason). A None status means "drop entirely" — a denied
    sender, where not even a row is worth keeping.
    """
    verdict = sender_verdict(rules, from_address)
    if verdict is SenderRuleAction.deny:
        return None, "denied_sender"
    if verdict is SenderRuleAction.allow:
        return ReviewStatus.imported, "allowed_sender"
    if confidence >= AUTO_IMPORT_CONFIDENCE:
        return ReviewStatus.imported, None
    return ReviewStatus.needs_review, "low_confidence"
