"""Bounded single-payment grammar with explicit clarification, not an LLM."""

import re
from pathlib import Path

from pharos_voice_action_gateway import ADDRESS_RE, strict_json_loads
from payment_preview import parse_payment_request, UNSUPPORTED_REQUEST_RE


CONTACTS_PATH = Path(__file__).resolve().parents[1] / "config/payment_contacts.json"


def load_contacts():
    contacts = strict_json_loads(CONTACTS_PATH.read_text(encoding="utf-8"))
    if (not isinstance(contacts, dict) or len(contacts) > 100 or any(
        not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", k)
        or not isinstance(v, str) or not ADDRESS_RE.fullmatch(v)
        or v != v.lower() or int(v, 16) == 0 for k, v in contacts.items()
    )):
        raise ValueError("Invalid server contact book")
    return contacts


def parse_mainnet_request(text, answers=None, contacts=None):
    answers = {} if answers is None else answers
    if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 500
            or not isinstance(answers, dict) or set(answers) - {"amount", "token", "recipient"}
            or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 100 for v in answers.values())):
        return {"status": "invalid", "message": "Invalid payment request or clarification"}
    contacts = load_contacts() if contacts is None else contacts
    # Check before contact substitution so names cannot erase a negation/cancel.
    if UNSUPPORTED_REQUEST_RE.search(text):
        return {"status": "invalid", "message": "Negated, cancelled or scheduled payments are not supported."}
    answers = {k: v.strip() for k, v in answers.items()}
    source = text.strip()
    source = re.sub(r"^(?:please\s+)?(?:could|can)\s+you\s+", "", source, flags=re.I)
    # "Pay alice 1 USDC" is an alternative order, but every word must match.
    reversed_form = re.fullmatch(
        r"(?:please\s+)?pay\s+([A-Za-z][A-Za-z0-9_.-]*)\s+(\d+(?:\.\d+)?)\s+([A-Za-z]+)[.!]?",
        source, re.I,
    )
    if reversed_form:
        recipient, amount, token = reversed_form.groups()
        source = f"Send {amount} {token} to {recipient}"

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
            chosen = effective.get("recipient", known)
            if chosen is None:
                return {"status": "needs_details", "field": "recipient",
                        "question": f'What is the full lowercase address for "{recipient}"?',
                        "reason": "No ENS lookup or guessed address; verify the payee independently."}
            if not ADDRESS_RE.fullmatch(chosen):
                return {"status": "needs_details", "field": "recipient",
                        "question": "Enter a full 0x address, not a name or a yes/no answer."}
            if known and chosen.lower() != known.lower():
                return {"status": "invalid", "message": "The recipient answer conflicts with the configured contact."}
            source = source[:recipient_match.start(1)] + chosen
            effective["recipient"] = chosen
    return parse_payment_request(source, effective)
