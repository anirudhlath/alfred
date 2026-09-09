import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TrackedAction } from "@/lib/actions";
import { pendingActionFixture } from "@/test/fixtures";
import { useActionRoute } from "./useActionRoute";

const { door, arrivedMock, answeredMock, openActionMock } = vi.hoisted(() => ({
  // What the Door already tracks; a test that needs the Door to know the id sets it.
  door: { actions: [] as TrackedAction[] },
  arrivedMock: vi.fn(),
  answeredMock: vi.fn(),
  openActionMock: vi.fn(),
}));

vi.mock("@/door/DoorProvider", () => ({
  useDoor: () => ({
    actions: door.actions,
    arrived: arrivedMock,
    answered: answeredMock,
    openAction: openActionMock,
  }),
}));

let status = 200;
const calls: string[] = [];

function Probe({ titles = {} }: { titles?: Record<string, string> }) {
  const { tombstone } = useActionRoute(titles);
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div>
      <span data-testid="path">{location.pathname}</span>
      <span data-testid="tomb">
        {tombstone && tombstone.kind === "tombstone"
          ? `${tombstone.title}|${tombstone.meta}`
          : "none"}
      </span>
      <button type="button" onClick={() => void navigate(-1)}>
        back
      </button>
      <button type="button" onClick={() => void navigate("/actions/a91f3c2e")}>
        again
      </button>
    </div>
  );
}

// The path is asserted whole: `/actions/…` contains `/`, so a substring match
// would pass before the hook had done anything.
const ROOT = /^\/$/;

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
  door.actions = [];
  arrivedMock.mockClear();
  answeredMock.mockClear();
  openActionMock.mockClear();
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
    // A Room already in the history: if the hook pushed instead of replacing,
    // going back would land on the decision again.
    render(
      <MemoryRouter initialEntries={["/", "/actions/a91f3c2e"]} initialIndex={1}>
        <Probe />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent(ROOT));
    expect(screen.getByTestId("tomb")).toHaveTextContent("none");

    // Synchronous on purpose: a push would put the decision back for one
    // render before the hook read it again, and a waitFor would forgive that.
    fireEvent.click(screen.getByText("back"));
    expect(screen.getByTestId("path")).toHaveTextContent(ROOT);
    expect(calls).toHaveLength(1);
  });

  it("reads once under StrictMode, and again on a later tap of the same id", async () => {
    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/actions/a91f3c2e"]}>
          <Probe />
        </MemoryRouter>
      </StrictMode>,
    );
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent(ROOT));
    expect(arrivedMock).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(1);

    // The same notification, tapped again while the app is still up.
    fireEvent.click(screen.getByText("again"));
    await waitFor(() => expect(arrivedMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent(ROOT));
    expect(calls).toHaveLength(2);
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
    expect(answeredMock).toHaveBeenCalledWith("a91f3c2e");
    expect(screen.getByTestId("path")).toHaveTextContent(ROOT);
  });

  it("leaves the tombstone to the Door when the Door already tracks the id", async () => {
    status = 404;
    door.actions = [{ action: pendingActionFixture, phase: "pending" }];
    renderAt("/actions/a91f3c2e", { a91f3c2e: "Lock unlock" });

    await waitFor(() => expect(answeredMock).toHaveBeenCalledWith("a91f3c2e"));
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent(ROOT));
    expect(screen.getByTestId("tomb")).toHaveTextContent("none");
  });

  it("claims nothing when the house could not be asked", async () => {
    status = 503;
    renderAt("/actions/a91f3c2e", { a91f3c2e: "Lock unlock" });

    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent(ROOT));
    expect(screen.getByTestId("tomb")).toHaveTextContent("none");
    expect(openActionMock).not.toHaveBeenCalled();
    expect(answeredMock).not.toHaveBeenCalled();
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
    // Still one read: the URL was replaced, so the location no longer names an id.
    expect(calls).toHaveLength(1);
  });

  it("does nothing at all in the Room", async () => {
    renderAt("/");
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent(ROOT));
    expect(calls).toHaveLength(0);
    expect(arrivedMock).not.toHaveBeenCalled();
  });

  it("ignores a path that is not a single action id", async () => {
    renderAt("/actions/a91f/extra");
    await waitFor(() => expect(screen.getByTestId("path")).toBeInTheDocument());
    expect(calls).toHaveLength(0);
  });
});
