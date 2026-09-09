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
let drainStatus = 200;

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      if (url === "/api/admin/notifications/deferred")
        return new Response(JSON.stringify(deferredFixture), { status: 200 });
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
  render(
    <QueryClientProvider client={client}>
      <HeldBackSheet open={open} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

beforeEach(() => {
  calls = [];
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

    expect(await screen.findByRole("button", { name: "Queued" })).toBeInTheDocument();
    expect(
      screen.getByText(
        /^Accepted at \d{2}:\d{2}\. Queue will empty on the next refresh if delivery succeeded\.$/,
      ),
    ).toBeInTheDocument();
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/drain"))).toBe(true);
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
