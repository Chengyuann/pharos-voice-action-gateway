"""Bounded single-payment grammar with explicit clarification, not an LLM."""

import re
from pathlib import Path
from evm_address import validate_address

from pharos_voice_action_gateway import ADDRESS_RE, strict_json_loads
from payment_preview import parse_payment_request, UNSUPPORTED_REQUEST_RE


CONTACTS_PATH = Path(__file__).resolve().parents[1] / "config/payment_contacts.json"
SMALL_NUMBERS = dict(zip(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(),
    range(20),
))
TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))


def spoken_amount(value):
    """Convert only exact decimal spelling; reject approximate and fractional phrases."""
    value = value.strip().lower()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return value
    if re.fullmatch(r"[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?", value):
        return value.replace(",", "")
    words = value.replace("-", " ").split()
    if not words or len(words) > 24:
        raise ValueError("Use one exact decimal amount")
    if "point" in words:
        if words.count("point") != 1:
            raise ValueError("Use one decimal point")
        index = words.index("point")
        fractional = words[index + 1:]
        if not fractional or any(w not in SMALL_NUMBERS or SMALL_NUMBERS[w] > 9 for w in fractional):
            raise ValueError("Say each decimal digit separately")
        whole = spoken_amount(" ".join(words[:index]) or "zero")
        if "." in whole:
            raise ValueError("Use one decimal point")
        return whole + "." + "".join(str(SMALL_NUMBERS[w]) for w in fractional)

    def under_hundred(part):
        if len(part) == 1 and part[0] in SMALL_NUMBERS:
            return SMALL_NUMBERS[part[0]]
        if part and part[0] in TENS:
            if len(part) == 1:
                return TENS[part[0]]
            if len(part) == 2 and part[1] in SMALL_NUMBERS and 0 < SMALL_NUMBERS[part[1]] < 10:
                return TENS[part[0]] + SMALL_NUMBERS[part[1]]
        raise ValueError("Use an exact amount, such as one hundred or 0.25")

    def under_thousand(part):
        if len(part) >= 2 and part[1] == "hundred" and part[0] in SMALL_NUMBERS and 0 < SMALL_NUMBERS[part[0]] < 10:
            rest = part[2:]
            if rest[:1] == ["and"]:
                rest = rest[1:]
                if not rest:
                    raise ValueError("Incomplete amount")
            return SMALL_NUMBERS[part[0]] * 100 + (under_hundred(rest) if rest else 0)
        return under_hundred(part)

    total, start, previous = 0, 0, 10**12
    for index, word in enumerate(words):
        if word not in {"billion", "million", "thousand"}:
            continue
        scale = {"billion": 10**9, "million": 10**6, "thousand": 1000}[word]
        if scale >= previous:
            raise ValueError("Ambiguous number scale")
        group = under_thousand(words[start:index])
        if not group:
            raise ValueError("Empty number group")
        total += group * scale
        previous, start = scale, index + 1
    if words[start:]:
        total += under_thousand(words[start:])
    elif start == 0:
        raise ValueError("Missing amount")
    return str(total)


def normalize_instruction(text):
    source = text.strip().rstrip(".!?。！？").strip()
    source = re.sub(
        r"^(?:please\s+)?(?:(?:could|can|would)\s+you\s+|I\s+(?:would\s+like|want|need)\s+to\s+|I'd\s+like\s+to\s+)",
        "", source, flags=re.I,
    )
    # A trailing courtesy is not part of an address. No other words are discarded.
    source = re.sub(r",?\s+please$", "", source, flags=re.I)
    chinese = re.fullmatch(
        r"(?:请)?(?:给|向)\s*([A-Za-z0-9_.-]+)\s*(?:转账|转|支付)\s*(\d+(?:\.\d+)?)\s*(PROS|USDC|WPROS)", source, re.I,
    )
    if chinese:
        recipient, amount, token = chinese.groups()
        source = f"Send {amount} {token} to {recipient}"
    reverse = re.fullmatch(
        r"(?:please\s+)?(?:pay|send|transfer)\s+([A-Za-z][A-Za-z0-9_.-]*)\s+(.+?)\s+(PROS|USDC|WPROS|PHRS)",
        source, re.I,
    )
    if reverse:
        recipient, amount, token = reverse.groups()
        source = f"Send {amount} {token} to {recipient}"
    missing = re.fullmatch(
        r"(?:please\s+)?pay\s+([A-Za-z][A-Za-z0-9_.-]*)\s+(PROS|USDC|WPROS|PHRS)", source, re.I,
    )
    if missing:
        source = f"Send {missing[2]} to {missing[1]}"
    amount_form = re.fullmatch(
        r"((?:please\s+)?(?:send|pay|transfer)\s+)(.+?)(\s+(?:PROS|USDC|WPROS|PHRS)(?:\s+to\s+\S+)?)",
        source, re.I,
    )
    if amount_form:
        prefix, raw, suffix = amount_form.groups()
        values = re.split(r"\s+or\s+", raw, flags=re.I)
        if len(values) > 2:
            raise ValueError("Choose one exact amount")
        source = prefix + " or ".join(spoken_amount(v) for v in values) + suffix
    return source


def validate_contacts(contacts):
    if not isinstance(contacts, dict) or len(contacts) > 100:
        raise ValueError("Invalid server contact book")
    normalized = {}
    for name, value in contacts.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
            raise ValueError("Invalid contact alias")
        candidates = [value] if isinstance(value, str) else value
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 5:
            raise ValueError("Contacts require 1-5 explicitly configured addresses")
        addresses = [validate_address(address) for address in candidates]
        if len(addresses) != len(set(addresses)):
            raise ValueError("Duplicate address in contact entry")
        normalized[name] = addresses[0] if len(addresses) == 1 else addresses
    return normalized


def load_contacts(path=None):
    return validate_contacts(strict_json_loads((path or CONTACTS_PATH).read_text(encoding="utf-8")))


def parse_mainnet_request(text, answers=None, contacts=None):
    answers = {} if answers is None else answers
    if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 500
            or not isinstance(answers, dict) or set(answers) - {"amount", "token", "recipient"}
            or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 100 for v in answers.values())):
        return {"status": "invalid", "message": "Invalid payment request or clarification"}
    contacts = load_contacts() if contacts is None else validate_contacts(contacts)
    # Check before contact substitution so names cannot erase a negation/cancel.
    if UNSUPPORTED_REQUEST_RE.search(text):
        return {"status": "invalid", "message": "Negated, cancelled or scheduled payments are not supported."}
    if len(re.findall(r"\b(?:send|pay|transfer)\b", text, re.I)) > 1:
        return {"status": "invalid", "message": "Use one instruction per payment; separate batch rows with semicolons."}
    answers = {k: v.strip() for k, v in answers.items()}
    if "recipient" in answers:
        try:
            validate_address(answers["recipient"])
        except ValueError as exc:
            return {"status": "invalid", "message": str(exc)}
    try:
        source = normalize_instruction(text)
        if "amount" in answers:
            answers["amount"] = spoken_amount(answers["amount"])
    except ValueError as exc:
        return {"status": "needs_details", "field": "amount", "requires_rewrite": True,
                "question": "Enter one exact amount, or rewrite the instruction.",
                "reason": str(exc)}
    # "Pay alice 1 USDC" is an alternative order, but every word must match.
    reversed_form = re.fullmatch(
        r"(?:please\s+)?pay\s+([A-Za-z][A-Za-z0-9_.-]*)\s+(?:(\d+(?:\.\d+)?)\s+)?([A-Za-z]+)[.!]?",
        source, re.I,
    )
    if reversed_form:
        recipient, amount, token = reversed_form.groups()
        source = f"Send {amount or ''} {token} to {recipient}"

    ambiguous_amount = re.fullmatch(
        r"(?:please\s+)?(?:send|pay|transfer)\s+(\d+(?:\.\d+)?)\s+or\s+"
        r"(\d+(?:\.\d+)?)\s+([A-Za-z]+)\s+to\s+(\S+)", source, re.I,
    )
    if ambiguous_amount:
        one, two, token, recipient = ambiguous_amount.groups()
        if "amount" not in answers:
            return {"status": "needs_details", "field": "amount",
                    "question": f"Which exact amount: {one} or {two}?",
                    "reason": "More than one amount was given."}
        if answers["amount"] not in {one, two}:
            return {"status": "needs_details", "field": "amount",
                    "question": f"Choose {one} or {two}, or rewrite the whole request."}
        source = f"Send {answers['amount']} {token} to {recipient}"
    # Dollar amounts do not imply USDC without an explicit answer.
    vague_currency = re.fullmatch(
        r"(?:please\s+)?(?:send|pay|transfer)\s+\$(\d+(?:\.\d+)?)\s+to\s+(\S+)",
        source, re.I,
    )
    if vague_currency:
        amount, recipient = vague_currency.groups()
        if "token" not in answers:
            return {"status": "needs_details", "field": "token",
                    "question": "Which asset do you mean: PROS, USDC or WPROS?",
                    "reason": "A dollar sign is not a token contract."}
        source = f"Send {amount} {answers['token']} to {recipient}"

    # Resolve an exact configured contact only. ENS-looking or unknown names need
    # a full address from the user, even if their prefix resembles a known name.
    recipient_match = re.search(r"\s+to\s+(\S+)$", source, re.I)
    effective = dict(answers)
    if recipient_match:
        recipient = recipient_match.group(1)
        if not recipient.lower().startswith("0x"):
            known = contacts.get(recipient.lower())
            if isinstance(known, list):
                chosen = effective.get("recipient")
                if chosen is None or chosen.lower() not in known:
                    return {"status": "needs_details", "field": "recipient",
                            "question": f'Multiple addresses for "{recipient}". Enter the exact intended address.',
                            "choices": list(known), "reason": "No address is chosen by list order."}
                known = chosen.lower()
            chosen = effective.get("recipient", known)
            if chosen is None:
                return {"status": "needs_details", "field": "recipient",
                        "question": f'What is the full address for "{recipient}"?',
                        "reason": "No ENS lookup or guessed address; verify the payee independently."}
            if not ADDRESS_RE.fullmatch(chosen):
                return {"status": "needs_details", "field": "recipient",
                        "question": "Enter a full 0x address, not a name or a yes/no answer."}
            if known and chosen.lower() != known.lower():
                return {"status": "invalid", "message": "The recipient answer conflicts with the configured contact."}
            source = source[:recipient_match.start(1)] + chosen
            effective["recipient"] = chosen
        else:
            try:
                validate_address(recipient)
            except ValueError as exc:
                return {"status": "invalid", "message": str(exc)}
    # Validate every address spelling before the generic parser's case-insensitive
    # conflict comparison. An invalid checksum must not be hidden by a valid answer.
    for match in re.finditer(r"0x[0-9a-fA-F]{40}", source):
        try:
            validate_address(match.group(0))
        except ValueError as exc:
            return {"status": "invalid", "message": str(exc)}
    return parse_payment_request(source, effective)
