import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { permission, reachContext, requestAndSubscribe } from "@/lib/push";
import { gateAnswered, REACH_ACTS_KEY, type ReachAct } from "@/lib/reach-local";
import { makeClient } from "@/test/push-client";
import { ReachGates, type ReachGate } from "./ReachGates";

vi.mock("@/lib/push", () => ({
  reachContext: vi.fn(),
  permission: vi.fn(),
  syncSubscription: vi.fn(),
  requestAndSubscribe: vi.fn(),
}));

const IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1";

function renderGates(gate: ReachGate, client = makeClient()) {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ReachGates gate={gate} signedInAt={null} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

/** Holds the gate in state, as Reach and the Room do, so a close starts the real leave. */
function Host() {
  const [gate, setGate] = useState<ReachGate | null>("standalone");
  return <ReachGates gate={gate} signedInAt={null} onClose={() => setGate(null)} />;
}

function renderHosted(client = makeClient()) {
  render(
    <QueryClientProvider client={client}>
      <Host />
    </QueryClientProvider>,
  );
}

/** A promise the test settles by hand, for what the gate says while iOS is asking. */
function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

/** The store takes no more writes, as a full or forbidden one does. */
function refuseWrites() {
  return vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("The quota has been exceeded.", "QuotaExceededError");
  });
}

/** What a tap's handler threw: React reports it to the window, and the tap goes on. */
function hearThrows() {
  const thrown = vi.fn();
  window.addEventListener("error", thrown);
  return thrown;
}

const allow = () => screen.getByRole("button", { name: "Let me reach you" });
const standalone = () => screen.queryByRole("dialog", { name: "You came in the front way" });

let thrown: ReturnType<typeof hearThrows>;

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(reachContext).mockReturnValue("capable");
  vi.mocked(permission).mockReturnValue("default");
  vi.mocked(requestAndSubscribe).mockResolvedValue("default");
  thrown = hearThrows();
});

afterEach(() => {
  window.removeEventListener("error", thrown);
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("ReachGates", () => {
  it("Let me reach you asks iOS, holds the button, and closes when the asking ends", async () => {
    const answer = deferred<NotificationPermission>();
    vi.mocked(requestAndSubscribe).mockReturnValue(answer.promise);
    const user = userEvent.setup();
    const onClose = renderGates("standalone");

    await user.click(allow());

    expect(requestAndSubscribe).toHaveBeenCalledTimes(1);
    expect(allow()).toHaveAttribute("aria-busy", "true");
    expect(onClose).not.toHaveBeenCalled();
    // The prompt is up: a second press asks nothing.
    await user.click(allow());
    expect(requestAndSubscribe).toHaveBeenCalledTimes(1);

    await act(async () => answer.resolve("granted"));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(allow()).not.toHaveAttribute("aria-busy");
  });

  it("closes its host's gate, with no complaint from React", async () => {
    const complaint = vi.spyOn(console, "error");
    const answer = deferred<NotificationPermission>();
    vi.mocked(requestAndSubscribe).mockReturnValue(answer.promise);
    const user = userEvent.setup();
    renderHosted();

    await user.click(allow());
    expect(standalone()).not.toBeNull();
    await act(async () => answer.resolve("denied"));

    await waitFor(() => expect(standalone()).toBeNull());
    // `onClose` sets the host's state, which React refuses from this one's render.
    expect(complaint).not.toHaveBeenCalled();
  });

  it("a tap during the gate's leave asks nothing and writes no second row", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(IPHONE);
    vi.mocked(requestAndSubscribe).mockResolvedValue("granted");
    const client = makeClient();
    const user = userEvent.setup();
    renderHosted(client);

    await user.click(allow());
    await waitFor(() => expect(standalone()).toHaveClass("rise-out"));
    expect(client.getQueryData<ReachAct[]>(REACH_ACTS_KEY)).toHaveLength(1);

    // Closing, but mounted and under the finger until its leave ends.
    fireEvent.click(allow());
    await act(async () => {});

    expect(requestAndSubscribe).toHaveBeenCalledTimes(1);
    expect(client.getQueryData<ReachAct[]>(REACH_ACTS_KEY)).toHaveLength(1);
  });

  it("an iPhone's grant through this gate lands the on row", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(IPHONE);
    vi.mocked(requestAndSubscribe).mockResolvedValue("granted");
    const client = makeClient();
    const user = userEvent.setup();
    const onClose = renderGates("standalone", client);

    await user.click(allow());

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(client.getQueryData<ReachAct[]>(REACH_ACTS_KEY)).toEqual([
      { at: expect.any(String), outcome: "on" },
    ]);
  });

  it("asks iOS before anything else in the tap", async () => {
    let markedFirst: boolean | null = null;
    vi.mocked(requestAndSubscribe).mockImplementation(async () => {
      markedFirst = gateAnswered();
      return "default";
    });
    const user = userEvent.setup();
    renderGates("standalone");

    await user.click(allow());

    expect(markedFirst).toBe(false);
  });

  it("a tap on Let me reach you ends the self-opening even if the dialog is dismissed", async () => {
    const user = userEvent.setup();
    const onClose = renderGates("standalone");
    expect(gateAnswered()).toBe(false);

    await user.click(allow());

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(gateAnswered()).toBe(true);
  });

  it("Not yet closes and ends the self-opening", async () => {
    const user = userEvent.setup();
    const onClose = renderGates("standalone");

    await user.click(screen.getByRole("button", { name: "Not yet" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(gateAnswered()).toBe(true);
    expect(requestAndSubscribe).not.toHaveBeenCalled();
  });

  it("the install gate's Not now only closes", async () => {
    const user = userEvent.setup();
    const onClose = renderGates("install");

    await user.click(screen.getByRole("button", { name: "Not now" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(gateAnswered()).toBe(false);
    expect(requestAndSubscribe).not.toHaveBeenCalled();
  });

  it("a throwing store does not stop the ask", async () => {
    const write = refuseWrites();
    const user = userEvent.setup();
    const onClose = renderGates("standalone");

    await user.click(allow());

    expect(write).toHaveBeenCalled();
    expect(requestAndSubscribe).toHaveBeenCalledTimes(1);
    expect(thrown).not.toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("a throwing store does not stop Not yet closing", async () => {
    const write = refuseWrites();
    const user = userEvent.setup();
    const onClose = renderGates("standalone");

    await user.click(screen.getByRole("button", { name: "Not yet" }));

    expect(write).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(thrown).not.toHaveBeenCalled();
  });
});
