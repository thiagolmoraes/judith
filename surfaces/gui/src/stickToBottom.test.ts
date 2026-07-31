import { describe, expect, it, vi } from "vitest";
import { stickToBottom } from "./stickToBottom";

// The bug this exists for: clicking a session left the transcript partway up its history,
// because the messages (and the markdown/images inside them) landed AFTER the one scroll
// that ran. These tests drive the schedulers by hand — no real time, no real layout.

/** A scroll container whose content height we control, mimicking late-arriving content. */
function fakeScroller(height = 100, clientHeight = 100) {
  return {
    scrollTop: 0,
    scrollHeight: height,
    clientHeight,
  } as unknown as HTMLElement & { scrollHeight: number; scrollTop: number };
}

/** Manual rAF: `flush()` runs exactly one frame, so growth can be interleaved. */
function manualRaf() {
  let next = 1;
  const queue = new Map<number, () => void>();
  return {
    raf: (cb: () => void) => {
      const id = next++;
      queue.set(id, cb);
      return id;
    },
    cancelRaf: (id: number) => queue.delete(id),
    flush() {
      const pending = [...queue.entries()];
      queue.clear();
      for (const [, cb] of pending) cb();
    },
    get pending() {
      return queue.size;
    },
  };
}

const noTimer = { setTimer: () => 0 as unknown, clearTimer: () => {} };

describe("stickToBottom", () => {
  it("lands at the bottom immediately, before any frame runs", () => {
    const el = fakeScroller(500);
    const r = manualRaf();
    stickToBottom(el, { shouldStick: () => true, ...r, ...noTimer });
    expect(el.scrollTop).toBe(500); // not waiting on a frame or an animation
  });

  it("follows content that arrives AFTER the first scroll", () => {
    // The actual bug: messages hydrate late, then markdown/images grow the transcript.
    const el = fakeScroller(300);
    const r = manualRaf();
    stickToBottom(el, { shouldStick: () => true, ...r, ...noTimer });
    expect(el.scrollTop).toBe(300);

    el.scrollHeight = 2400; // the session's messages land
    r.flush();
    expect(el.scrollTop).toBe(2400);

    el.scrollHeight = 2900; // an image decodes, a tool card expands
    r.flush();
    expect(el.scrollTop).toBe(2900);
  });

  it("stops pinning the moment the user scrolls away", () => {
    const el = fakeScroller(300);
    const r = manualRaf();
    let stick = true;
    stickToBottom(el, { shouldStick: () => stick, ...r, ...noTimer });

    stick = false; // the user scrolled up
    el.scrollTop = 40;
    el.scrollHeight = 5000;
    r.flush();

    expect(el.scrollTop).toBe(40); // never yanked back
    expect(r.pending).toBe(0); // and the loop is gone, not spinning
  });

  it("gives up after the timeout so it can't pin forever", () => {
    const el = fakeScroller(300);
    const r = manualRaf();
    let fire = () => {};
    stickToBottom(el, {
      shouldStick: () => true,
      ...r,
      setTimer: (cb) => {
        fire = cb;
        return 1;
      },
      clearTimer: () => {},
    });

    fire(); // the settling window closes
    el.scrollHeight = 9000;
    r.flush();

    expect(el.scrollTop).toBe(300); // late growth after the window is the user's business
    expect(r.pending).toBe(0);
  });

  it("cleanup is idempotent and halts the loop", () => {
    const el = fakeScroller(300);
    const r = manualRaf();
    const stop = stickToBottom(el, { shouldStick: () => true, ...r, ...noTimer });

    stop();
    stop(); // a second call (React StrictMode double-invokes cleanups) must be harmless

    el.scrollHeight = 9000;
    r.flush();
    expect(el.scrollTop).toBe(300);
    expect(r.pending).toBe(0);
  });

  it("does not thrash when the height is stable", () => {
    const el = fakeScroller(700);
    const r = manualRaf();
    stickToBottom(el, { shouldStick: () => true, ...r, ...noTimer });

    el.scrollTop = 690; // a 10px nudge the user made; height unchanged
    r.flush();
    expect(el.scrollTop).toBe(690); // no reason to move: nothing grew
  });

  it("uses the real schedulers when none are injected", () => {
    const el = fakeScroller(400);
    const raf = vi.spyOn(globalThis, "requestAnimationFrame").mockReturnValue(1 as never);
    try {
      const stop = stickToBottom(el, { shouldStick: () => true });
      expect(el.scrollTop).toBe(400);
      expect(raf).toHaveBeenCalled();
      stop();
    } finally {
      raf.mockRestore();
    }
  });
});
