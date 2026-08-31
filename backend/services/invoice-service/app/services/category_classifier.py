"""Rule-based, best-guess category assignment at scan time — P1-E: reuses
the shared deterministic Candidate -> evidence -> select_candidate
architecture (invoice_extraction.candidates) the AI Engine's own extraction
pipeline already uses for vendor/invoice_date/total (docs/invoice-ocr-plan.md
§§20-23), applied here to category. Same "rules engine, no LLM" philosophy
as the rest of this pipeline: a keyword hit against the vendor name, every
line item's description, and the filename is the only evidence — no ML, no
embeddings, no semantic similarity.

P1-E's own audit (docs/invoice-ocr-plan.md §24) found the previous
implementation's "first category (checked in a fixed priority order) with
any keyword hit wins" was the exact same architectural flaw §20-23 already
fixed for other fields: a document whose vendor is a bakery *and* whose own
item description says "Cake" has real, deterministic evidence for *both*
"Office Entertainment" (a bakery is a food venue) and "Employee Care" (a
cake is a specific occasion/gift item) — the old code could only ever
return the first category in its own fixed list that had any hit at all,
regardless of which category's evidence was actually stronger.

Every category candidate here is scored from *all* of its own matching
keywords, tiered by how diagnostic each one is: a specific occasion/
product/service word ("cake", "petrol", "salary", "toner") is real evidence
of *why* a purchase was made; a generic vendor-*type* word ("bakeshop",
"restaurant", "hardware store") only says what *kind* of business this is,
which is necessarily weaker (many different purchases at a bakery are
possible) and never allowed to outscore a genuine occasion/product signal
from a different category. Matching is still word-boundary-aware plain
text search (never a raw substring test — see `_contains_keyword`'s own
docstring for the real false-positive this closes), still fully auditable,
still never invents a category when the evidence is absent or genuinely
tied between two categories (see `classify_category`'s own docstring).
"""
import re
from dataclasses import dataclass

from invoice_extraction.candidates import Candidate, select_candidate

#: How far a winning category candidate must beat its nearest rival to
#: count as a confident ACCEPT rather than REVIEW — mirrors invoice_
#: extraction's own vendor/invoice_date/total ambiguity margins. Category
#: scores are much coarser (a handful of discrete tiers, not a continuous
#: OCR-confidence range), so this is set well below a single keyword hit's
#: own WEAK-tier score, catching only a genuine, near-exact tie between two
#: categories' own evidence.
_AMBIGUITY_MARGIN = 1.0

#: A specific occasion/product/service word is real, direct evidence of
#: *why* a purchase was made ("cake" for a specific gift, "petrol" for
#: vehicle fuel, "salary" for a payroll disbursement) — the strongest
#: deterministic signal this classifier has, since it names the actual
#: thing bought or the actual purpose, not merely the shape of business
#: that sold it.
_STRONG_SCORE = 10.0

#: A generic vendor-*type*/venue-category word ("bakeshop", "restaurant",
#: "hardware store", the generic-store fallback) only establishes what
#: *kind* of business issued the receipt — real evidence, but weaker than
#: a specific product/occasion word, since the same venue type can
#: legitimately serve more than one real category (a bakery sells routine
#: snacks *and* farewell cakes; a hardware store sells maintenance
#: supplies *and*, in principle, other things). Deliberately never allowed
#: to outscore even a single STRONG hit from a rival category — see
#: `_CATEGORY_KEYWORDS`' own per-category `strong`/`weak` split below.
_WEAK_SCORE = 5.0

#: A second (or third...) matching keyword within the *same* category is
#: itself corroborating evidence — Phase 7's own "multiple supporting
#: terms" signal ("Pens, Folders, Staples, Printer Paper" together should
#: support a category more confidently than any one of those words alone).
#: Applied to STRONG matches as a plain linear bonus — several genuinely
#: specific, independent signals reinforcing the *same* category is real,
#: additive evidence, and two categories each accumulating their own
#: strong matches is a genuine ambiguity `_AMBIGUITY_MARGIN` is meant to
#: catch, not something to suppress here.
_MULTI_TERM_BONUS = 2.0

#: WEAK matches use a *diminishing* bonus instead (found live, P1-E.1: once
#: raw OCR text is also searched, a single document can easily mention a
#: generic venue word — "restaurant", "bakery", "bakeshop" — two or three
#: times over, and a plain linear bonus let that accumulation reach 19.0,
#: comfortably past a single genuine STRONG hit's own 10.0 from a rival
#: category — exactly the "pile of incidental weak matches out-
#: accumulating a specific signal" this tier was always meant to prevent,
#: but the arithmetic didn't actually enforce). Each additional weak match
#: contributes half of the previous one's own bonus, a converging
#: geometric series whose sum has a strict supremum of `_WEAK_SCORE +
#: 2 * _MULTI_TERM_BONUS` — for the constants below, 5.0 + 4.0 = 9.0,
#: which no finite number of weak-only matches can ever reach, still
#: comfortably under a single STRONG hit's 10.0, regardless of how many
#: times a generic venue word happens to repeat across a whole document.
def _weak_category_score(match_count: int) -> float:
    if match_count <= 0:
        return 0.0
    total = _WEAK_SCORE
    bonus = _MULTI_TERM_BONUS
    for _ in range(match_count - 1):
        total += bonus
        bonus /= 2.0
    return total


@dataclass(frozen=True)
class _CategoryKeywords:
    category: str
    #: Specific occasion/product/service words — see the module docstring
    #: for why these outrank `weak` below.
    strong: tuple[str, ...] = ()
    #: Generic vendor-type/venue-category words.
    weak: tuple[str, ...] = ()


# Every keyword the previous, unscored implementation had is preserved
# below (P1-E's own audit — docs/invoice-ocr-plan.md §24 — classifies each
# one explicitly); nothing was dropped except the stale "papajohns"
# work-around (see that section for why removing it is safe). New
# additions are commented individually with the same "found live, general,
# not vendor-specific" standard this project has held itself to throughout
# P1-B/C/D.
_CATEGORY_KEYWORDS: tuple[_CategoryKeywords, ...] = (
    _CategoryKeywords(
        "Office Entertainment",
        # Specific food/drink brand and dish names — real evidence of what
        # was actually bought, not just what kind of venue sold it.
        strong=(
            "kfc", "mcdonald", "domino", "papa johns", "papa john's", "biryani", "karahi", "burger",
            "hardees", "subway",
            # P1-E: common Pakistani-market soft-drink brand names — found
            # live (a real "Cash & Carry" grocery-type vendor's own line
            # items were "Sprite", "Mirinda", "Pepsi": specific product
            # names, general market brands, not tied to this one vendor)
            # — a petty-cash purchase of named soft drinks is refreshments,
            # not the generic-store fallback category a "cash & carry"
            # vendor name alone would otherwise suggest.
            "pepsi", "sprite", "mirinda", "cola", "7up", "fanta",
        ),
        # Generic food/beverage *venue*-type words — a real venue, but not
        # itself evidence of a specific dish/occasion the way the above is.
        weak=(
            "restaurant", "cafe", "coffee", "catering", "snack", "food court", "pizza",
            "bakeshop", "bake shop", "bakery",
        ),
    ),
    _CategoryKeywords(
        "Employee Care",
        # Every word here already names a specific gift/occasion, not a
        # venue — "cake" in particular is what correctly lets a farewell/
        # birthday bakery purchase outscore that same receipt's own
        # "bakeshop" (Office Entertainment, weak) evidence, closing the
        # exact rule-order bug this phase's own audit found (§24).
        strong=("farewell", "birthday", "cake", "bouquet", "flowers", "gift basket", "celebration", "welcome gift"),
        # P1-E: "florist" — a real business-*type* word (a florist's own
        # petty-cash purchases in an office context are essentially always
        # gifting/celebration-related), general across any florist, not
        # this one vendor. Kept weak, not strong: a florist's own generic
        # vendor-type signal is exactly the same tier as "bakeshop" above,
        # for the same reason.
        weak=("florist",),
    ),
    _CategoryKeywords(
        "Employee Training & Education",
        strong=("coursera", "udemy", "training", "workshop", "seminar", "certification", "bootcamp", "course fee"),
    ),
    _CategoryKeywords(
        "Legal & Professional",
        # P1-E.1: bare "fbr" removed — found live once raw_text exposed it:
        # "FBR Invoice#.:147160..." is a *mandatory* Pakistani e-invoice
        # compliance stamp printed on virtually every POS receipt in this
        # domain, regardless of what was purchased — universal regulatory
        # boilerplate, not evidence of a legal/professional-services
        # transaction, the same class of false positive Phase 8 already
        # excludes generic financial vocabulary for. "ntn registration"
        # (a business specifically offering NTN-registration consultancy)
        # is a genuinely different, far more specific phrase and stays.
        strong=(
            "lawyer", "advocate", "notary", "attorney", "legal fee", "audit fee", "consultant", "consultancy",
            "professional fee", "ntn registration",
        ),
    ),
    _CategoryKeywords(
        "Vehicle Running & Maintenance",
        strong=("petrol", "diesel", "fuel station", "car wash", "vehicle service", "oil change", "tyre", "mechanic"),
        # "filling station" — found live: "FSO DHA Filling Station", a
        # correctly-extracted real vendor; the standard Pakistani term for
        # a petrol pump, general across any such vendor, not this one.
        weak=("filling station",),
    ),
    _CategoryKeywords(
        "Office Repair & Maintenance",
        strong=(
            "repair", "maintenance", "maintinance", "plumber", "electrician", "carpenter", "generator service",
            "ac service", "air conditioner service", "power plug",
        ),
        weak=(
            "toilet",
            # P1-E: "hardware store"/"electric & hardware" — found live:
            # "Azeem Electric & Hardware Store", a correctly-extracted real
            # vendor with zero prior keyword coverage despite its own line
            # items (a toilet repair fitting, a power plug) squarely
            # matching this category — a hardware store's typical petty-
            # cash purchase in an office context is repair/maintenance
            # supplies, the same general "vendor-type implies category"
            # reasoning the pre-existing generic-store fallback already
            # uses for "Office / Misc Supplies" below, not specific to
            # this one shop.
            "hardware store", "electric & hardware",
        ),
    ),
    _CategoryKeywords(
        "Office / Misc Supplies",
        strong=("water bottle", "mineral water", "grocery", "cleaning", "detergent", "sanitizer", "tissue", "floor mat"),
        # The pre-existing generic-store fallback — a business-*type*
        # shape, not a product keyword (see its own extended docstring
        # note below on why it must stay weakest of all).
        weak=("mart", "cash & carry", "general store", "super store", "convenience store"),
    ),
    _CategoryKeywords(
        "Stationary / Others",
        strong=("stationery", "stationary", "printer ink", "toner", "notebook", "stapler", "photocopy"),
    ),
    _CategoryKeywords("Salary", strong=("salary", "wages", "payroll", "stipend")),
    _CategoryKeywords(
        "Utilities",
        strong=("electricity bill", "wapda", "k-electric", "sui gas", "gas bill", "internet bill", "ptcl", "broadband"),
    ),
    _CategoryKeywords("Rent", strong=("rent", "lease agreement")),
    _CategoryKeywords(
        "Marketing",
        strong=("advertising", "marketing", "facebook ads", "google ads", "promotion", "billboard", "branding"),
    ),
    _CategoryKeywords(
        "Fuel & Transport",
        # P1-E.1: bare "shipping" removed — found live once raw_text
        # exposed it: a florist's own standard invoice template printed a
        # "Shipping" *field label* (blank/unfilled) alongside "Sub Total"/
        # "Grand Total", the same universal billing-template vocabulary as
        # those two — never itself evidence the underlying purchase was a
        # courier/transport service. "cargo"/"courier"/named carrier
        # brands stay: specific enough that they don't double as a generic
        # template field the way bare "shipping" does.
        strong=("uber", "careem", "bykea", "courier", "tcs", "leopard courier", "cargo"),
    ),
    _CategoryKeywords(
        "Travel", strong=("air ticket", "flight booking", "hotel booking", "travel agency", "visa fee"),
    ),
)

#: A whole-word match only — a plain substring test (the previous
#: implementation) let "rent" match inside "different"/"current"/"parent"/
#: "torrent", and "toilet" match inside "toiletries"; both are real,
#: general false-positive risks (not observed yet in the Golden Dataset,
#: the same "found by direct construction, not assumed" standard P1-B/C/D
#: held themselves to for the vendor/date/total collisions they each
#: found). `\b` anchors each keyword's own two ends to real word
#: boundaries; a multi-word keyword's internal space still matches
#: normally since `\b` never applies there. Matches the exact "leading
#: space on 'mart'" trick the old table used for the identical reason —
#: this generalizes it to every keyword at once, so "mart" itself
#: (no longer needing to be written " mart") still rejects "Smart".
_WORD_BOUNDARY_CACHE: dict[str, "re.Pattern[str]"] = {}


def _contains_keyword(haystack: str, keyword: str) -> bool:
    pattern = _WORD_BOUNDARY_CACHE.get(keyword)
    if pattern is None:
        pattern = re.compile(rf"\b{re.escape(keyword)}\b")
        _WORD_BOUNDARY_CACHE[keyword] = pattern
    return pattern.search(haystack) is not None


def _category_candidates(haystack: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    for entry in _CATEGORY_KEYWORDS:
        evidence: dict[str, float] = {}
        strong_matches = [kw for kw in entry.strong if _contains_keyword(haystack, kw)]
        weak_matches = [kw for kw in entry.weak if _contains_keyword(haystack, kw)]
        for i, keyword in enumerate(strong_matches):
            evidence[f"strong:{keyword}"] = _STRONG_SCORE + (_MULTI_TERM_BONUS if i > 0 else 0.0)
        if weak_matches:
            # Each keyword's own key holds its *incremental* contribution
            # (first match 5.0, second +2.0, third +1.0, ...) so the keys
            # sum to exactly _weak_category_score(len(weak_matches)) — kept
            # per-keyword, not one pooled total, for the same explainability
            # every other evidence entry in this codebase already has.
            previous_total = 0.0
            for i, keyword in enumerate(weak_matches):
                running_total = _weak_category_score(i + 1)
                evidence[f"weak:{keyword}"] = running_total - previous_total
                previous_total = running_total
        if evidence:
            candidates.append(Candidate(field_name="category", value=entry.category, method="keyword_match", evidence=evidence))
    return candidates


def classify_category(
    *, vendor_name: str | None, item_descriptions: list[str | None], filename: str | None,
    raw_text: str | None = None,
) -> str | None:
    """Best-guess category for a freshly-scanned purchase invoice, or None
    if the evidence is absent or genuinely tied between two categories —
    never a guess, the same "absent means not found" principle Invoice.
    field_confidence already uses elsewhere in this model.

    A category candidate is generated for every category with at least one
    keyword hit, scored by `_category_candidates` above, then judged by the
    exact same shared `select_candidate` (invoice_extraction.candidates)
    vendor/invoice_date/total already use: `"unknown"` (no category has any
    evidence at all) and `"review"` (the top two categories' own scores are
    within `_AMBIGUITY_MARGIN` of each other — a genuine tie this
    classifier cannot safely break) both return `None` here rather than a
    guess, since this field has no separate confidence/evidence channel of
    its own to report an uncertain-but-present value through (unlike
    vendor/invoice_date/total's own FieldValue) — "no category assigned,
    a human decides" is this field's own honest equivalent.

    `raw_text` (P1-E.1, docs/invoice-ocr-plan.md §25) is the document's own
    full OCR text — a *fallback* source, consulted only when vendor_name/
    item_descriptions/filename together produce no category evidence at
    all, never blended in alongside them by default. It exists to reach
    genuine category evidence a document states only in free prose (a
    cash-receipt voucher's own "received amount Rs. 6,000/- as Salary/
    Stipend"), which never populates a structured vendor/line-item field at
    all — the same "prefer structured evidence, fall back to a broader
    scan only when nothing else exists" precedence total/vendor extraction
    already use elsewhere in this pipeline (find_total_anywhere/
    _dynamic_total).

    This precedence is deliberate, not incidental: P1-E.1's own Golden
    Dataset re-run found that searching all four sources together let
    raw_text — the widest, least-curated source by far — override a
    genuinely correct structured-evidence result on a real document (a
    company's own idiosyncratic bookkeeping convention filed a Coursera
    purchase under "Office / Misc Supplies", not "Employee Training &
    Education" — the ledger's own judgment call, not something any keyword
    scan of the receipt itself could recover; raw_text's own "coursera"
    mention overrode the correct answer vendor_name/item_descriptions
    alone had already reached). Falling back only when the structured
    sources are silent keeps raw_text's real, wider reach without letting
    it outrank evidence that was already sufficient on its own.

    Every source is still scored by the exact same keyword table — no
    separate evidence tier, no new false-positive surface beyond what that
    table's own existing strong/weak split and word-boundary matching
    already guard against: a generic financial word (invoice/total/tax/
    payment/amount/receipt) is still never itself a keyword in any
    category, and a genuinely conflicting signal elsewhere in the same
    text still routes to the same `_AMBIGUITY_MARGIN`-guarded review/
    `None` outcome as any other genuine tie.
    """
    structured_haystack = " ".join(
        text.lower() for text in [vendor_name, filename, *item_descriptions] if text
    )
    if structured_haystack:
        result = select_candidate(_category_candidates(structured_haystack), ambiguity_margin=_AMBIGUITY_MARGIN)
        if result.status == "accept" and result.winner is not None:
            return result.winner.value
        if result.status == "review":
            # A genuine tie *within* the structured evidence itself — this
            # classifier already cannot safely break it; raw_text (wider,
            # noisier, never meant to override a real structured signal)
            # is not consulted to arbitrarily decide it either.
            return None
    if not raw_text:
        return None
    fallback_result = select_candidate(_category_candidates(raw_text.lower()), ambiguity_margin=_AMBIGUITY_MARGIN)
    if fallback_result.status != "accept" or fallback_result.winner is None:
        return None
    # A fallback winner backed by *only* weak (generic vendor-type) keyword
    # evidence, with vendor_name/item_descriptions having found nothing at
    # all, is exactly the "weak evidence -> review/UNKNOWN, never a guess"
    # case this classifier's own core principle demands — found live: a
    # bakery vendor recovered only via raw_text (structured vendor
    # extraction had failed) landed on "Office Entertainment" via "bakeshop"
    # alone, the same *already-acknowledged* structural ambiguity (a bakery
    # purchase can genuinely be routine "Office Entertainment" or a specific
    # occasion "Employee Care" — see TestRuleOrderBugFixed's own docstring)
    # that a *specific* occasion/product word (strong) correctly resolves.
    # Without one, this fallback path has no way to tell those two apart,
    # so it must not confidently pick either.
    if not any(key.startswith("strong:") for key in fallback_result.winner.evidence):
        return None
    return fallback_result.winner.value
