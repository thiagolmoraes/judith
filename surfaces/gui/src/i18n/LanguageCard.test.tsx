import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nProvider, useI18n } from "./useLocale";
import { LOCALES, LOCALE_NAMES } from "./index";

// The language picker as it appears in Settings, rendered standalone so the switching
// behaviour can be tested without the rest of the settings page.
function Picker() {
  const { locale, setLocale, t } = useI18n();
  return (
    <div role="radiogroup" aria-label={t("settings.language.title")}>
      {LOCALES.map((code) => (
        <button
          key={code}
          role="radio"
          aria-checked={code === locale}
          data-testid={`locale-${code}`}
          onClick={() => void setLocale(code)}
        >
          {LOCALE_NAMES[code]}
        </button>
      ))}
      <span data-testid="sample">{t("common.cancel")}</span>
    </div>
  );
}

type Call = { url: string; method: string; body: any };

function stubFetch(settings: Record<string, unknown>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: (init?.method || "GET").toUpperCase(),
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (url.includes("/v1/settings/locale")) {
        return { ok: true, json: async () => ({ ok: true }) } as Response;
      }
      return { ok: true, json: async () => settings } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("language picker", () => {
  it("announces which language is selected", async () => {
    // A radiogroup of plain buttons reads the labels but never the selection, leaving a
    // screen-reader user unable to tell the current language.
    stubFetch({ locale: "en" });
    render(
      <I18nProvider>
        <Picker />
      </I18nProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("locale-en").getAttribute("aria-checked")).toBe("true"),
    );
    expect(screen.getByTestId("locale-pt-BR").getAttribute("aria-checked")).toBe("false");
    expect(screen.getAllByRole("radio")).toHaveLength(LOCALES.length);
  });

  it("starts from the stored preference", async () => {
    stubFetch({ locale: "pt-BR" });
    render(
      <I18nProvider>
        <Picker />
      </I18nProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("sample").textContent).toBe("Cancelar"));
  });

  it("switches the interface and persists the choice", async () => {
    const calls = stubFetch({ locale: "en" });
    render(
      <I18nProvider>
        <Picker />
      </I18nProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("sample").textContent).toBe("Cancel"));

    fireEvent.click(screen.getByTestId("locale-pt-BR"));
    // Switches immediately rather than waiting on the write — an unchanged UI while a
    // request flies reads as "the click didn't work".
    await waitFor(() => expect(screen.getByTestId("sample").textContent).toBe("Cancelar"));
    await waitFor(() => {
      const post = calls.find((c) => c.url.includes("/v1/settings/locale"));
      expect(post?.body).toEqual({ locale: "pt-BR" });
    });
  });

  // These two assert an English result, which is also the pre-fetch default — so they
  // have to prove the read actually COMPLETED, or they'd pass against a fetch that never
  // resolves. Each holds the response open, checks a pt-BR control renders on the same
  // deferred stub, then settles it and asserts English.
  const deferred = <T,>() => {
    let settle!: (value: T) => void;
    let fail!: (reason: unknown) => void;
    const promise = new Promise<T>((resolve, reject) => {
      settle = resolve;
      fail = reject;
    });
    return { promise, settle, fail };
  };

  it("ignores an unknown stored locale rather than rendering blank", async () => {
    const gate = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn(() => gate.promise));
    render(
      <I18nProvider>
        <Picker />
      </I18nProvider>,
    );
    await act(async () => {
      gate.settle({ ok: true, json: async () => ({ locale: "kl-GL" }) } as Response);
    });
    // Resolved with an unusable locale — English, and specifically not blank.
    expect(screen.getByTestId("sample").textContent).toBe("Cancel");
    expect(screen.getByTestId("locale-en").getAttribute("aria-checked")).toBe("true");
  });

  it("still renders when settings can't be read", async () => {
    const gate = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn(() => gate.promise));
    render(
      <I18nProvider>
        <Picker />
      </I18nProvider>,
    );
    await act(async () => {
      gate.fail(new Error("offline"));
    });
    // The rejection was handled and the app is still up — not stuck mid-render.
    expect(screen.getByTestId("sample").textContent).toBe("Cancel");
    expect(screen.getByTestId("locale-pt-BR")).toBeTruthy();
  });

  it("the deferred stub can produce a non-English result", async () => {
    // Guards the two tests above: if the deferred fetch never reached the provider, this
    // would render English too, and their assertions would prove nothing.
    const gate = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn(() => gate.promise));
    render(
      <I18nProvider>
        <Picker />
      </I18nProvider>,
    );
    expect(screen.getByTestId("sample").textContent).toBe("Cancel"); // pre-resolution
    await act(async () => {
      gate.settle({ ok: true, json: async () => ({ locale: "pt-BR" }) } as Response);
    });
    expect(screen.getByTestId("sample").textContent).toBe("Cancelar");
  });

  it("keeps the switch when persisting fails", async () => {
    // The write is what makes it survive a restart; losing it shouldn't also undo the
    // language the user just picked in front of them.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/v1/settings/locale")) throw new Error("write failed");
        return { ok: true, json: async () => ({ locale: "en" }) } as Response;
      }),
    );
    render(
      <I18nProvider>
        <Picker />
      </I18nProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("sample").textContent).toBe("Cancel"));
    fireEvent.click(screen.getByTestId("locale-pt-BR"));
    await waitFor(() => expect(screen.getByTestId("sample").textContent).toBe("Cancelar"));
  });
});
