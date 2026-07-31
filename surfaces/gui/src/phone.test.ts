import { describe, expect, it } from "vitest";
import { formatPhone, normalizePhone } from "./phone";

// The table mirrors tests/test_phone.py case for case. The two implementations only stay
// in agreement if they are tested against the same rows: the allow-list stores bare digits
// (what Evolution gives us as `user_id`), so a number the owner types here has to reduce to
// the SAME string a webhook would produce, or the person is authorized in name only.

describe("normalizePhone", () => {
  it.each([
    ["5511999999999", "5511999999999"], // already normalized
    ["+55 11 99999-9999", "5511999999999"], // the shape a human types
    ["(11) 99999-9999", "5511999999999"], // local BR: country code implied
    ["011999999999", "5511999999999"], // trunk prefix dropped
    ["+1 415 555 0199", "14155550199"], // non-BR passes through
    ["5511999999999@s.whatsapp.net", "5511999999999"], // pasted JID
  ])("accepts what people actually type: %s", (raw, expected) => {
    expect(normalizePhone(raw)).toBe(expected);
  });

  it.each([["", "   ", "abc", "12", "+", "55", "1".repeat(20), "(11) 9999-999"]].flat())(
    "rejects garbage and impossible lengths: %s",
    (raw) => {
      expect(normalizePhone(raw)).toBeNull();
    },
  );

  it("is idempotent", () => {
    const once = normalizePhone("+55 (11) 99999-9999");
    expect(once).not.toBeNull();
    expect(normalizePhone(once!)).toBe(once);
  });
});

describe("formatPhone", () => {
  it.each([
    ["5511999999999", "+55 11 99999-9999"], // BR mobile (9 digits)
    ["551133334444", "+55 11 3333-4444"], // BR landline (8 digits)
    ["14155550199", "+1 415 555 0199"], // US
    ["999999999999999", "+999999999999999"], // unknown shape: digits, one plus
  ])("is readable and never lossy: %s", (digits, expected) => {
    expect(formatPhone(digits)).toBe(expected);
    // Whatever we display must normalize back to what we store.
    expect([digits, null]).toContain(normalizePhone(formatPhone(digits)));
  });

  it("passes through non-numbers untouched", () => {
    // Group JIDs and ids we can't parse must render as-is rather than as a bad phone.
    expect(formatPhone("")).toBe("");
    expect(formatPhone("120363@g.us")).toBe("120363@g.us");
  });
});
