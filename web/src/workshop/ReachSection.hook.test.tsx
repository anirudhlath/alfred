import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  permission,
  reachContext,
  requestAndSubscribe,
  syncSubscription,
} from "@/lib/push";
import { REACH_ACTS_KEY, type ReachAct } from "@/lib/reach-local";
import { authSession, IPHONE_UA } from "@/test/fixtures";
import { BUSY, failedClient, makeClient } from "@/test/push-client";
import { ReachSection } from "./ReachSection";
import type { Sessions } from "./useSystem";

// The card over its own hook. `ReachSection.test.tsx` hands it a `PushValue`,
// so it can only prove the row draws what it is given; whether opening the
// bench syncs is a fact about the observer the card mounts. The real `usePush`
// runs here, over a mocked `@/lib/push`, as in `usePush.test.tsx`.
vi.mock("@/lib/push", () => ({
  reachContext: vi.fn(),
  permission: vi.fn(),
  syncSubscription: vi.fn(),
  requestAndSubscribe: vi.fn(),
}));

/** The page's notification permission, which a test changes as the owner would. */
let granted: NotificationPermission;

const SESSIONS: Sessions = {
  list: [authSession({ current: true })],
  read: true,
  ended: {},
  ending: {},
  failed: {},
  end: () => {},
  error: null,
};

const FAILED_SUB = "off · allowed in iOS · the house did not take the registration";

function renderReach(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <ReachSection sessions={SESSIONS} />
    </QueryClientProvider>,
  );
}

/** Let every queued microtask run without asserting anything happened. */
const settle = async (): Promise<void> => {
  await act(async () => {
    await Promise.resolve();
  });
};

beforeEach(() => {
  vi.resetAllMocks();
  granted = "granted";
  vi.mocked(reachContext).mockReturnValue("capable");
  vi.mocked(permission).mockImplementation(() => granted);
  vi.mocked(syncSubscription).mockResolvedValue("on");
  // The owner says yes, and the browser's permission follows a tick after the ask.
  vi.mocked(requestAndSubscribe).mockImplementation(async () => {
    await Promise.resolve();
    granted = "granted";
    return "granted";
  });
});

describe("ReachSection over usePush", () => {
  it.each([false, true])(
    "opens on a failed sync without syncing, and Try again syncs (after an on: %s)",
    async (afterOn) => {
      const user = userEvent.setup();
      renderReach(await failedClient(afterOn));
      await settle();

      expect(syncSubscription).not.toHaveBeenCalled();
      expect(screen.getByText(FAILED_SUB)).toBeInTheDocument();
      expect(screen.getByText(BUSY)).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Try again" }));

      await waitFor(() => expect(screen.getByText("on")).toBeInTheDocument());
      expect(syncSubscription).toHaveBeenCalledTimes(1);
    },
  );

  it("enables from its own observer, registering once", async () => {
    granted = "default";
    const user = userEvent.setup();
    renderReach(makeClient());

    await user.click(screen.getByRole("button", { name: "Enable" }));
    await user.click(screen.getByRole("button", { name: "Let me reach you" }));

    await waitFor(() => expect(screen.getByText("on")).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(requestAndSubscribe).toHaveBeenCalledTimes(1);
    // Enable registered for itself and the query took its answer. This mock answers
    // inside a microtask, which leaves no render to sync in; a real, slower answer
    // can leave another observer room to (`usePush`).
    expect(syncSubscription).not.toHaveBeenCalled();
  });

  describe("on an iPhone, Reach's route to the Act rows", () => {
    afterEach(() => {
      vi.restoreAllMocks();
      localStorage.clear();
    });

    /** The owner taps through Reach and answers iOS with `answer`, on `client`. */
    async function answerFromReach(answer: "granted" | "denied", client: QueryClient) {
      granted = "default";
      vi.spyOn(navigator, "userAgent", "get").mockReturnValue(IPHONE_UA);
      vi.mocked(requestAndSubscribe).mockImplementation(async () => {
        await Promise.resolve();
        granted = answer;
        return answer;
      });
      const user = userEvent.setup();
      renderReach(client);

      await user.click(screen.getByRole("button", { name: "Enable" }));
      await user.click(screen.getByRole("button", { name: "Let me reach you" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    }

    it("Enable then Let me reach you writes the on row", async () => {
      const client = makeClient();

      await answerFromReach("granted", client);

      expect(client.getQueryData<ReachAct[]>(REACH_ACTS_KEY)).toEqual([
        { at: expect.any(String), outcome: "on" },
      ]);
    });

    it("Enable then Let me reach you, declined writes the off row", async () => {
      const client = makeClient();

      await answerFromReach("denied", client);

      expect(client.getQueryData<ReachAct[]>(REACH_ACTS_KEY)).toEqual([
        { at: expect.any(String), outcome: "off" },
      ]);
    });
  });
});
