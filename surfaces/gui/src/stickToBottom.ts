// Opening a session must land on the LAST message.
//
// Scrolling once when the messages arrive is not enough: they come from an async fetch,
// and the transcript keeps GROWING after that first paint as markdown renders, images
// decode and tool cards lay out. A scroll that ran before the growth lands partway up
// the history — exactly the "I clicked the session and had to scroll to the end"
// complaint. So instead of scrolling once, pin to the bottom until the height stops
// changing, then let go.
//
// Watching content height (not the element box) is what makes this work: the scroller
// fills its pane and never resizes, so a ResizeObserver on it would never fire.

export interface StickOptions<THandle = number> {
  /** Stop pinning once this many ms have passed — the pin exists to survive layout
   * settling, never to fight the user. */
  timeoutMs?: number;
  /** False cancels the pin immediately (the user scrolled away). */
  shouldStick: () => boolean;
  /** Schedulers, injected so tests drive time instead of waiting on it. */
  raf?: (cb: () => void) => number;
  cancelRaf?: (handle: number) => void;
  // The timer handle differs between DOM (number) and Node (Timeout), so it is a type
  // parameter: whatever `setTimer` hands back is exactly what `clearTimer` takes.
  setTimer?: (cb: () => void, ms: number) => THandle;
  clearTimer?: (handle: THandle) => void;
}

/**
 * Keep `el` scrolled to its bottom while its content height keeps changing.
 * Returns a cleanup that stops the pin (safe to call twice).
 */
export function stickToBottom<THandle = number>(
  el: HTMLElement,
  opts: StickOptions<THandle>,
): () => void {
  const {
    timeoutMs = 1500,
    shouldStick,
    raf = requestAnimationFrame,
    cancelRaf = cancelAnimationFrame,
    setTimer = setTimeout as unknown as (cb: () => void, ms: number) => THandle,
    clearTimer = clearTimeout as unknown as (handle: THandle) => void,
  } = opts;

  const jump = () => {
    el.scrollTop = el.scrollHeight;
  };

  jump(); // the arrival scroll: instant, so nothing has to outrun late layout
  let lastHeight = el.scrollHeight;
  let frame: number | null = null;
  let stopped = false;

  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (frame !== null) cancelRaf(frame);
    clearTimer(timer);
  };

  const tick = () => {
    if (stopped) return;
    if (!shouldStick()) {
      stop(); // the user took over — never yank them back
      return;
    }
    if (el.scrollHeight !== lastHeight) {
      lastHeight = el.scrollHeight;
      jump();
    }
    frame = raf(tick);
  };

  const timer = setTimer(stop, timeoutMs);
  frame = raf(tick);
  return stop;
}
