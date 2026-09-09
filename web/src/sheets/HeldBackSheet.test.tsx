import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deferredFixture } from "@/test/fixtures";
import { HeldBackSheet } from "./HeldBackSheet";

interface Call {
  url: string;
  method: string;
}

let calls: Call[] = [];
let deferredStatus = 200;
let drainStatus = 200;

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      if (url === "/api/admin/notifications/deferred")
        return new Response(
          deferredStatus === 200 ? JSON.stringify(deferredFixture) : '{"detail":"store unavailable"}',
          { status: deferredStatus },
        );
      if (url === "/api/admin/notifications/drain")
        return new Response(
          drainStatus === 200 ? '{"status":"queued"}' : '{"detail":"redis is down"}',
          { status: drainStatus },
        );
      return new Response("{}", { status: 404 });
    }),
  );
}

function renderSheet(open = true) {
  const onClose = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (isOpen: boolean) => (
    <QueryClientProvider client={client}>
      <HeldBackSheet open={isOpen} onClose={onClose} />
    </QueryClientProvider>
  );
  const view = render(tree(open));
  return { onClose, setOpen: (isOpen: boolean) => view.rerender(tree(isOpen)) };
}

beforeEach(() => {
  calls = [];
  deferredStatus = 200;
  drainStatus = 200;
  stubFetch();
});

afterEach(() => vi.unstubAllGlobals());

describe("HeldBackSheet", () => {
  it("explains why the queue exists and what never drains it", async () => {
    renderSheet();
    expect(
      await screen.findByText(
        "Non-urgent notifications wait here while do-not-disturb is on. Urgent ones still speak. With no expiry set this queue never drains on its own.",
      ),
    ).toBeInTheDocument();
  });

  it("lists what is waiting, with its urgency and its hour", async () => {
    renderSheet();

    expect(await screen.findByText("Bins go out tonight")).toBeInTheDocument();
    expect(screen.getByText("important · 07:02 · deferred by DND")).toBeInTheDocument();
    expect(screen.getByText("Bathroom humidity stayed high")).toBeInTheDocument();
    expect(screen.getByText("informational · 07:19 · deferred by DND")).toBeInTheDocument();
  });

  it("says queued, not delivered", async () => {
    const user = userEvent.setup();
    renderSheet();
    const drain = await screen.findByRole("button", { name: "Drain queue now" });

    expect(
      screen.getByText(
        "Queued only; the server does not report delivery. Items stay listed until a fresh read confirms.",
      ),
    ).toBeInTheDocument();

    await user.click(drain);

    // Disabled, so it cannot be double-queued; announced, so a screen reader
    // hears the outcome — the label alone does not change on a failure.
    expect(await screen.findByRole("button", { name: "Queued" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      /^Accepted at \d{2}:\d{2}\. Queue will empty on the next refresh if delivery succeeded\.$/,
    );
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/drain"))).toBe(true);
  });

  it("offers the drain again on the next visit", async () => {
    const user = userEvent.setup();
    const { setOpen } = renderSheet();
    await user.click(await screen.findByRole("button", { name: "Drain queue now" }));
    await screen.findByRole("button", { name: "Queued" });

    setOpen(false);
    // The reset is on the opening edge: the 380 ms leave must not flash the idle button back.
    expect(screen.getByRole("button", { name: "Queued" })).toBeDisabled();
    setOpen(true);

    expect(await screen.findByRole("button", { name: "Drain queue now" })).toBeEnabled();
    expect(screen.queryByText(/^Accepted at/)).toBeNull();
  });

  it("forgets a refused drain on the next visit too", async () => {
    const user = userEvent.setup();
    drainStatus = 503;
    const { setOpen } = renderSheet();
    await user.click(await screen.findByRole("button", { name: "Drain queue now" }));
    await screen.findByText("redis is down");

    setOpen(false);
    setOpen(true);

    expect(await screen.findByText(/^Queued only;/)).toBeInTheDocument();
    expect(screen.queryByText("redis is down")).toBeNull();
  });

  it("keeps the rows on screen after draining, because nothing is confirmed", async () => {
    const user = userEvent.setup();
    renderSheet();
    await user.click(await screen.findByRole("button", { name: "Drain queue now" }));

    await screen.findByRole("button", { name: "Queued" });
    expect(screen.getByText("Bins go out tonight")).toBeInTheDocument();
  });

  it("reports a refused drain and stays offerable", async () => {
    const user = userEvent.setup();
    drainStatus = 503;
    renderSheet();

    await user.click(await screen.findByRole("button", { name: "Drain queue now" }));

    expect(await screen.findByText("redis is down")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Drain queue now" })).toBeEnabled();

    // And the retry does not keep the old failure under its own success.
    drainStatus = 200;
    await user.click(screen.getByRole("button", { name: "Drain queue now" }));

    expect(await screen.findByRole("button", { name: "Queued" })).toBeInTheDocument();
    expect(screen.queryByText("redis is down")).toBeNull();
  });

  it("has words for a drain that fails without any", async () => {
    const user = userEvent.setup();
    renderSheet();
    const drain = await screen.findByRole("button", { name: "Drain queue now" });
    // A rejection that is not an Error has no `.message` to read.
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject("no words")));

    await user.click(drain);

    expect(await screen.findByText("Something went wrong.")).toBeInTheDocument();
  });

  it("says so when nothing is waiting", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"notifications":[]}', { status: 200 })),
    );
    renderSheet();

    expect(await screen.findByText("Nothing is being held back.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Drain queue now" })).toBeNull();
  });

  it("does not call the queue empty before it has read it", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    renderSheet();

    expect(screen.getByText(/^Non-urgent notifications wait here/)).toBeInTheDocument();
    expect(screen.queryByText("Nothing is being held back.")).toBeNull();
  });

  it("says so when the queue cannot be read", async () => {
    deferredStatus = 503;
    renderSheet();

    expect(await screen.findByRole("status")).toHaveTextContent("store unavailable");
    expect(screen.queryByText("Nothing is being held back.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Drain queue now" })).toBeNull();
  });

  it("closes on Done", async () => {
    const user = userEvent.setup();
    const { onClose } = renderSheet();

    await user.click(await screen.findByRole("button", { name: "Done" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("reads nothing while it is closed", () => {
    renderSheet(false);
    expect(calls).toHaveLength(0);
  });
});
