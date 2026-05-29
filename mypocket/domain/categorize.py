"""Rule-based transaction categorization.

Rules are (pattern, category, subcategory) tuples evaluated in order; first match wins.
Patterns are case-insensitive regex matched against the transaction description.
The user can extend these via the CategoryRule table at runtime (TODO: UI).
"""

import re
from dataclasses import dataclass


@dataclass
class CategoryMatch:
    category: str
    subcategory: str | None = None
    merchant: str | None = None


# Order matters: more specific patterns first.
DEFAULT_RULES: list[tuple[str, str, str | None]] = [
    # --- Income ---
    (r"\b(payroll|direct dep|direct deposit|salary)\b", "Income", "Salary"),
    (r"\b(interest payment|interest earned|interest paid)\b", "Income", "Interest"),
    (r"\bdividend\b", "Income", "Dividends"),
    (r"\b(refund|reimburs|cashback|cash back)\b", "Income", "Refunds"),
    # --- Transfers ---
    (r"\b(zelle|venmo|cash app|paypal)\b", "Transfers", "P2P"),
    (r"\b(transfer|xfer|withdrawal to|deposit from)\b", "Transfers", None),
    (r"\bach\b", "Transfers", "ACH"),
    # --- Food & Drink ---
    (r"\b(starbucks|dunkin|peet|blue bottle|philz)\b", "Food & Drink", "Coffee"),
    (
        r"\b(whole foods|trader joe|safeway|kroger|wegmans|publix|aldi|sprouts|costco|h-e-b)\b",
        "Food & Drink",
        "Groceries",
    ),
    (r"\b(uber eats|doordash|grubhub|seamless|caviar|postmates)\b", "Food & Drink", "Delivery"),
    (
        r"\b(chipotle|shake shack|sweetgreen|mcdonald|burger king|wendy|chick-fil|taco bell|"
        r"panera|five guys|in-n-out|domino|pizza hut)\b",
        "Food & Drink",
        "Fast Food",
    ),
    (r"\b(restaurant|grill|cafe|bistro|kitchen|tavern|bar & grill)\b", "Food & Drink", "Restaurants"),
    # --- Transport ---
    (r"\b(uber|lyft)\b", "Transport", "Rideshare"),
    (r"\b(shell|exxon|chevron|bp|mobil|texaco|76 |sunoco|valero|arco)\b", "Transport", "Gas"),
    (r"\b(mta|bart|metro|subway|amtrak|metro-?north|lirr)\b", "Transport", "Public Transit"),
    (r"\b(parking|park\.?\s)\b", "Transport", "Parking"),
    (r"\b(delta|united|american airlines|jetblue|southwest|spirit|alaska)\b", "Transport", "Flights"),
    # --- Shopping ---
    (r"\b(amazon|amzn)\b", "Shopping", "Amazon"),
    (r"\b(target|walmart|wal-mart)\b", "Shopping", "Big Box"),
    (r"\b(apple\.com|apple store)\b", "Shopping", "Electronics"),
    (r"\b(best buy|microcenter|newegg)\b", "Shopping", "Electronics"),
    # --- Subscriptions / Entertainment ---
    (
        r"\b(netflix|hulu|spotify|apple music|disney plus|disney\+|hbo max|max\.com|youtube premium|"
        r"prime video|paramount|peacock)\b",
        "Subscriptions",
        "Streaming",
    ),
    (
        r"\b(openai|chatgpt|anthropic|claude|github|gitlab|notion|linear|vercel|figma|adobe)\b",
        "Subscriptions",
        "Software",
    ),
    (r"\b(planet fitness|equinox|peloton|gym|fitness)\b", "Subscriptions", "Fitness"),
    # --- Bills / Utilities ---
    (
        r"\b(con\s?ed|consolidated edison|pg&e|pge|duke energy|nationalgrid|national grid|"
        r"electric|gas company)\b",
        "Bills",
        "Utilities",
    ),
    (r"\b(verizon|at&t|t-mobile|tmobile|comcast|xfinity|spectrum)\b", "Bills", "Phone/Internet"),
    (r"\b(rent|landlord|property mgmt)\b", "Bills", "Rent"),
    (r"\b(insurance|geico|state farm|progressive|allstate)\b", "Bills", "Insurance"),
    # --- Cash ---
    (r"\b(atm|cash withdraw|withdrawal)\b", "Cash", None),
    # --- Fees ---
    (r"\b(fee|charge|service charge|overdraft|nsf)\b", "Fees", None),
    # --- Investments (E*TRADE side) ---
    (r"\b(bought|buy)\b", "Investment", "Buy"),
    (r"\b(sold|sell)\b", "Investment", "Sell"),
    (r"\b(reinvest|drip)\b", "Investment", "Reinvest"),
    (r"\bcontribution\b", "Investment", "Contribution"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), c, s) for p, c, s in DEFAULT_RULES]


def categorize(
    description: str,
    amount: float | None = None,
    account_type: str | None = None,
) -> CategoryMatch:
    """Categorize a transaction. `account_type` shapes the fallback for
    unmatched positives: refunds on a credit card aren't income.

    P2P platforms (Venmo, Zelle, etc.) are handled directionally:
      • outflow → Transfers > P2P (you sent money — washes out for analytics)
      • inflow  → Reimbursements > P2P (someone paid you back — offsets spending,
                                         but NOT counted as income).
    """
    if not description:
        return CategoryMatch(category="Uncategorized")

    for pattern, category, subcategory in _COMPILED:
        if pattern.search(description):
            merchant = _extract_merchant(description)
            # P2P inflows are reimbursements (you got paid back for something),
            # not generic transfers.
            if category == "Transfers" and subcategory == "P2P" and amount is not None and amount > 0:
                return CategoryMatch(category="Reimbursements", subcategory="P2P", merchant=merchant)
            return CategoryMatch(category=category, subcategory=subcategory, merchant=merchant)

    merchant = _extract_merchant(description)
    # No rule matched. Fall back based on sign + account type:
    if amount is not None and amount > 0:
        # Positive on a credit card = refund or payment, not income.
        if account_type == "credit":
            return CategoryMatch(category="Refunds", merchant=merchant)
        return CategoryMatch(category="Income", subcategory="Other", merchant=merchant)
    return CategoryMatch(category="Uncategorized", merchant=merchant)


def _extract_merchant(description: str) -> str:
    """Best-effort: strip trailing transaction noise to get a readable merchant name."""
    s = description.strip()
    # Drop trailing store numbers, dates, transaction codes
    s = re.sub(r"\s+#\d+.*$", "", s)
    s = re.sub(r"\s+\d{2}/\d{2}.*$", "", s)
    s = re.sub(r"\s+(POS|DEBIT|CREDIT|PURCHASE|PAYMENT)\b.*$", "", s, flags=re.IGNORECASE)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s)
    return s[:80]
