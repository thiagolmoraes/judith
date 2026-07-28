import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SettingsView } from "./SettingsView";
import { showPersonas } from "../flags";

// Persona management shipped hidden behind a localStorage flag, which meant the only way
// to turn it on was DevTools. This card exposes it. The reload is deliberate: flags.ts
// reads the flag at render time and nothing subscribes to it, so the nav and the
// new-session menu would keep their old shape until something else re-rendered.

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.removeItem("ocw.flag.personas");
});

function renderSettings() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => ({}) }) as Response),
  );
  render(<SettingsView />);
}

describe("Experimental flags card", () => {
  it("reflects the flag's current state", () => {
    localStorage.setItem("ocw.flag.personas", "1");
    renderSettings();
    const toggle = screen.getByTestId("experimental-card").querySelector("button");
    expect(toggle?.getAttribute("aria-checked") ?? toggle?.getAttribute("aria-pressed")).toBe(
      "true",
    );
  });

  it("writes the flag and reloads when switched on", () => {
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    localStorage.setItem("ocw.flag.personas", "0"); // start from an explicit off
    renderSettings();

    expect(showPersonas()).toBe(false);
    fireEvent.click(screen.getByTestId("experimental-card").querySelector("button")!);

    expect(localStorage.getItem("ocw.flag.personas")).toBe("1");
    expect(showPersonas()).toBe(true);
    // Without this the Personas tab stays hidden until an unrelated re-render.
    expect(reload).toHaveBeenCalled();
  });

  it("is on by default, with no key stored", () => {
    // The shipped default flipped to on: new personas (Assistant) ship disabled, and
    // Settings ▸ Personas is the only place to enable them — hiding that tab made them
    // unreachable without DevTools.
    localStorage.removeItem("ocw.flag.personas");
    expect(showPersonas()).toBe(true);
  });

  it("writes \"0\" when switched off, not just removing the key", () => {
    // "0" force-hides; a missing key falls back to the shipped default. Today those
    // agree, but flipping the default later would silently re-show the feature for
    // anyone who had turned it off.
    localStorage.setItem("ocw.flag.personas", "1");
    vi.stubGlobal("location", { ...window.location, reload: vi.fn() });
    renderSettings();

    fireEvent.click(screen.getByTestId("experimental-card").querySelector("button")!);
    expect(localStorage.getItem("ocw.flag.personas")).toBe("0");
    expect(showPersonas()).toBe(false);
  });
});
