// Phone numbers as the allow-list sees them — the browser half of
// `coworker/connectors/phone.py`, kept deliberately identical.
//
// Contact rows arrive already formatted from the server, but a number the owner TYPES
// never reaches Python before we POST it to /allow. If this reduction disagreed with the
// server's by one digit, the owner would authorize a key no webhook ever produces and the
// person would still be parked. Hence a port rather than a call: same rules, same result.

const DIGITS_RE = /\d+/g;
const BR_CC = "55";
// E.164 allows 15 digits max; below ~10 there is no country+area+line to speak of.
const MIN_DIGITS = 10;
const MAX_DIGITS = 15;
// A local BR number is 10 (landline) or 11 (mobile, leading 9) digits: area code + line.
const BR_LOCAL_LENGTHS = [10, 11];

/** Digits-only key for the allow-list, or null when `raw` can't be a phone number.
 *
 * Accepts what people paste: `+55 11 99999-9999`, `(11) 99999-9999`, a full JID. */
export function normalizePhone(raw: string): string | null {
  if (!raw) return null;
  // A pasted JID carries the number in front of the suffix.
  const head = raw.split("@", 1)[0];
  // A leading "+" means the number already carries its country code — decisive, because
  // "+1 415 555 0199" is 11 digits, the same length as a BR mobile written locally.
  const explicitCc = head.trimStart().startsWith("+");
  let digits = (head.match(DIGITS_RE) ?? []).join("");
  if (!digits) return null;
  // Trunk prefix: "011999999999" → "11999999999" (only when what follows still looks
  // like a local number, so a legitimate leading 0 country-ish string isn't mangled).
  if (digits.startsWith("0") && BR_LOCAL_LENGTHS.includes(digits.length - 1)) {
    digits = digits.slice(1);
  }
  // No country code (local BR): prepend it, so the key matches the webhook's.
  // LENGTH decides, never the leading digits: area code 55 (Santa Maria/RS) makes
  // "55 99123-4567" start with the country code it is still missing, and skipping it
  // there would store a key no webhook ever produces.
  if (!explicitCc && BR_LOCAL_LENGTHS.includes(digits.length)) {
    digits = BR_CC + digits;
  }
  if (digits.length < MIN_DIGITS || digits.length > MAX_DIGITS) return null;
  return digits;
}

/** Readable form for display only. Anything that isn't a plain number (a group JID, an
 * unparseable id) comes back untouched — better a raw id than a wrong phone. */
export function formatPhone(digits: string): string {
  if (!digits || !/^\d+$/.test(digits)) return digits;
  if (digits.startsWith(BR_CC) && (digits.length === 12 || digits.length === 13)) {
    const area = digits.slice(2, 4);
    const line = digits.slice(4);
    const half = line.length === 9 ? 5 : 4; // mobile (9 digits) vs landline (8)
    return `+${BR_CC} ${area} ${line.slice(0, half)}-${line.slice(half)}`;
  }
  if (digits.startsWith("1") && digits.length === 11) {
    // NANP
    return `+1 ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`;
  }
  return `+${digits}`;
}
