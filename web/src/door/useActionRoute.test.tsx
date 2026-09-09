import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pendingActionFixture } from "@/test/fixtures";
import { useActionRoute } from "./useActionRoute";

const { arrivedMock, openActionMock } = vi.hoisted(() => ({
  arrivedMock: vi.fn(),
  openActionMock: vi.fn(),
}));

vi.mock("@/door/DoorProvider", () => ({
  useDoor: () => ({ arrived: arrivedMock, openAction: openActionMock }),
}));

let status = 200;
const calls: string[] = [];

function Probe({ titles = {} }: { titles?: Record<string, string> }) {
  const { tombstone } = useActionRoute(titles);
  const location = useLocation();
  return (
    <div>
      <span data-testid="path">{location.pathname}</span>
      <span data-testid="tomb">
        {tombstone && tombstone.kind === "tombstone"
          ? `${tombstone.title}|${tombstone.meta}`
          : "none"}
      </span>
    </div>
  );
}

function renderAt(path: string, titles?: Record<string, string>) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Probe titles={titles} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  status = 200;
  calls.length = 0;
  arrivedMock.mockClear();
  openActionMock.mockClear();
  // Inside the fixture's fuse, as DoorProvider.test pins it.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-09-07T07:42:00Z"));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(
        status === 200
          ? JSON.stringify(pendingActionFixture)
          : '{"detail":"Pending action not found or expired"}',
        { status },
      );
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useActionRoute", () => {
  it("opens the approval a notification named", async () => {
    renderAt("/actions/a91f3c2e");

    await waitFor(() => expect(arrivedMock).toHaveBeenCalledWith(pendingActionFixture));
    expect(openActionMock).toHaveBeenCalledWith("a91f3c2e");
    expect(calls).toEqual(["/api/actions/a91f3c2e"]);
  });

  it("replaces the URL so a refresh does not reopen it", async () => {
    renderAt("/actions/a91f3c2e");
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/"));
    expect(screen.getByTestId("tomb")).toHaveTextContent("none");
  });

  it("lands on a tombstone when the house has already forgotten it", async () => {
    status = 404;
    renderAt("/actions/a91f3c2e", { a91f3c2e: "Lock unlock" });

    await waitFor(() =>
      expect(screen.getByTestId("tomb")).toHaveTextContent(
        "Lock unlock|already answered · nothing was done",
      ),
    );
    expect(openActionMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("path")).toHaveTextContent("/");
  });

  it("names an unremembered approval by its short id", async () => {
    status = 404;
    renderAt("/actions/a91f3c2e");

    await waitFor(() =>
      expect(screen.getByTestId("tomb")).toHaveTextContent("Action a91f|already answered"),
    );
  });

  it("keeps the tombstone once the history learns the name", async () => {
    status = 404;
    const { rerender } = render(
      <MemoryRouter initialEntries={["/actions/a91f3c2e"]}>
        <Probe titles={{}} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("tomb")).toHaveTextContent("Action a91f"));

    rerender(
      <MemoryRouter initialEntries={["/actions/a91f3c2e"]}>
        <Probe titles={{ a91f3c2e: "Lock unlock" }} />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByTestId("tomb")).toHaveTextContent("Lock unlock"));
    // Still one read: the id was handled the first time.
    expect(calls).toHaveLength(1);
  });

  it("does nothing at all in the Room", async () => {
    renderAt("/");
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/"));
    expect(calls).toHaveLength(0);
    expect(arrivedMock).not.toHaveBeenCalled();
  });

  it("ignores a path that is not a single action id", async () => {
    renderAt("/actions/a91f/extra");
    await waitFor(() => expect(screen.getByTestId("path")).toBeInTheDocument());
    expect(calls).toHaveLength(0);
  });
});
