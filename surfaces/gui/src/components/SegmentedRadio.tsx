import { useRef } from "react";

// The segmented control used for Theme, Language and PDF fallback.
//
// These started as plain <button>s inside a role="radiogroup", which announces the labels
// but not the selection, and leaves keyboard behaviour as buttons: every option lands in
// the Tab order and arrow keys do nothing. A radio group is supposed to be ONE tab stop
// that arrows move within, so this implements the WAI-ARIA radio pattern properly —
// roving tabindex plus arrow/Home/End handling — in one place, rather than three copies
// each getting it slightly wrong.
//
// Native <input type="radio"> would give this for free, but the existing `seg` styling is
// built around buttons and rewriting it is a bigger change than this component.

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  /** Optional per-option test id; the group's `testIdPrefix` is used when absent. */
  testId?: string;
}

export function SegmentedRadio<T extends string>({
  options,
  value,
  onChange,
  label,
  className = "seg mt-2.5",
  testIdPrefix,
  groupTestId,
}: {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (next: T) => void;
  /** Accessible name for the group — what a screen reader reads before the options. */
  label: string;
  className?: string;
  testIdPrefix?: string;
  groupTestId?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const index = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );

  const focusAndSelect = (next: number) => {
    const wrapped = (next + options.length) % options.length;
    onChange(options[wrapped].value);
    // Move focus with the selection: in a radio group the focused option IS the selected
    // one, which is what makes arrow navigation legible to a screen reader.
    refs.current[wrapped]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        event.preventDefault();
        focusAndSelect(index + 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        event.preventDefault();
        focusAndSelect(index - 1);
        break;
      case "Home":
        event.preventDefault();
        focusAndSelect(0);
        break;
      case "End":
        event.preventDefault();
        focusAndSelect(options.length - 1);
        break;
    }
  };

  return (
    <div className={className} role="radiogroup" aria-label={label} data-testid={groupTestId}>
      {options.map((option, i) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            // Roving tabindex: one tab stop for the whole group, arrows move within it.
            tabIndex={selected ? 0 : -1}
            className={selected ? "active" : ""}
            data-testid={option.testId ?? (testIdPrefix ? `${testIdPrefix}-${option.value}` : undefined)}
            onClick={() => onChange(option.value)}
            onKeyDown={onKeyDown}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
