import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SegmentedRadio } from "./SegmentedRadio";

const OPTIONS = [
  { value: "light" as const, label: "Light" },
  { value: "dark" as const, label: "Dark" },
  { value: "auto" as const, label: "Auto" },
];

function setup(value: "light" | "dark" | "auto" = "light") {
  const onChange = vi.fn();
  render(
    <SegmentedRadio
      label="Appearance"
      value={value}
      options={OPTIONS}
      onChange={onChange}
      testIdPrefix="theme"
    />,
  );
  return onChange;
}

afterEach(cleanup);

describe("SegmentedRadio", () => {
  it("announces the group and which option is selected", () => {
    setup("dark");
    expect(screen.getByRole("radiogroup", { name: "Appearance" })).toBeTruthy();
    expect(screen.getByTestId("theme-dark").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("theme-light").getAttribute("aria-checked")).toBe("false");
  });

  it("is a single tab stop, not one per option", () => {
    // The whole point of the radio pattern: Tab reaches the group, arrows move inside it.
    // As plain buttons, all three sat in the tab order.
    setup("dark");
    expect(screen.getByTestId("theme-dark").getAttribute("tabindex")).toBe("0");
    expect(screen.getByTestId("theme-light").getAttribute("tabindex")).toBe("-1");
    expect(screen.getByTestId("theme-auto").getAttribute("tabindex")).toBe("-1");
  });

  it("selects with arrow keys in both directions", () => {
    const onChange = setup("dark");
    fireEvent.keyDown(screen.getByTestId("theme-dark"), { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("auto");
    fireEvent.keyDown(screen.getByTestId("theme-dark"), { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("light");
    // Down/Up are equivalent, since the group may be laid out either way.
    fireEvent.keyDown(screen.getByTestId("theme-dark"), { key: "ArrowDown" });
    expect(onChange).toHaveBeenLastCalledWith("auto");
  });

  it("wraps around at both ends", () => {
    const onChange = setup("auto"); // last option
    fireEvent.keyDown(screen.getByTestId("theme-auto"), { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("light");

    cleanup();
    const onChange2 = setup("light"); // first option
    fireEvent.keyDown(screen.getByTestId("theme-light"), { key: "ArrowLeft" });
    expect(onChange2).toHaveBeenLastCalledWith("auto");
  });

  it("supports Home and End", () => {
    const onChange = setup("dark");
    fireEvent.keyDown(screen.getByTestId("theme-dark"), { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith("auto");
    fireEvent.keyDown(screen.getByTestId("theme-dark"), { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith("light");
  });

  it("moves focus with the selection", () => {
    // In a radio group the focused option is the selected one; without this the arrow
    // keys change the value while focus stays behind, which a screen reader can't follow.
    setup("light");
    fireEvent.keyDown(screen.getByTestId("theme-light"), { key: "ArrowRight" });
    expect(document.activeElement).toBe(screen.getByTestId("theme-dark"));
  });

  it("still selects on click", () => {
    const onChange = setup("light");
    fireEvent.click(screen.getByTestId("theme-auto"));
    expect(onChange).toHaveBeenCalledWith("auto");
  });

  it("ignores unrelated keys", () => {
    const onChange = setup("light");
    fireEvent.keyDown(screen.getByTestId("theme-light"), { key: "a" });
    fireEvent.keyDown(screen.getByTestId("theme-light"), { key: "Tab" });
    expect(onChange).not.toHaveBeenCalled();
  });
});
