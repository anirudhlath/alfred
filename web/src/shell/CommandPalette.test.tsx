import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { CommandPalette } from "./CommandPalette";

// Mock react-router navigate
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => mockNavigate };
});

// Mock api post
const mockPost = vi.fn().mockResolvedValue({});
vi.mock("@/lib/api", () => ({ post: (...args: unknown[]) => mockPost(...args) }));

// Mock sonner toast
vi.mock("sonner", () => ({ toast: vi.fn(), Toaster: () => null }));

// Polyfill ResizeObserver for cmdk (not available in jsdom)
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

// Mock the command UI components to avoid cmdk + Dialog jsdom issues
vi.mock("@/components/ui/command", async () => {
  const React = await import("react");

  interface WithChildrenAndClass { children?: React.ReactNode; className?: string }
  interface CommandDialogProps extends WithChildrenAndClass {
    open?: boolean;
    onOpenChange?: (open: boolean) => void;
  }
  interface CommandInputProps {
    placeholder?: string;
    className?: string;
  }
  interface CommandItemProps extends WithChildrenAndClass {
    onSelect?: () => void;
  }
  interface CommandGroupProps extends WithChildrenAndClass {
    heading?: string;
  }

  return {
    Command: ({ children }: WithChildrenAndClass) => React.createElement("div", { "data-testid": "command" }, children),
    CommandDialog: ({ open, children }: CommandDialogProps) =>
      open ? React.createElement("div", { "data-testid": "command-dialog" }, children) : null,
    CommandInput: ({ placeholder }: CommandInputProps) =>
      React.createElement("input", { placeholder, "data-testid": "command-input" }),
    CommandList: ({ children }: WithChildrenAndClass) =>
      React.createElement("div", { "data-testid": "command-list" }, children),
    CommandEmpty: ({ children }: WithChildrenAndClass) =>
      React.createElement("div", { "data-testid": "command-empty" }, children),
    CommandGroup: ({ heading, children }: CommandGroupProps) =>
      React.createElement("div", { "data-testid": `command-group-${heading ?? ""}` }, children),
    CommandItem: ({ onSelect, children }: CommandItemProps) =>
      React.createElement("button", { onClick: onSelect, "data-testid": "command-item" }, children),
  };
});

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPalette() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CommandPalette", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPost.mockResolvedValue({});
  });

  it("is closed by default", () => {
    renderPalette();
    expect(screen.queryByTestId("command-dialog")).not.toBeInTheDocument();
  });

  it("opens on ⌘K", async () => {
    renderPalette();
    await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(screen.getByTestId("command-dialog")).toBeInTheDocument();
  });

  it("navigates and closes when a Go-to item is selected", async () => {
    renderPalette();
    await userEvent.keyboard("{Meta>}k{/Meta}");
    await userEvent.click(screen.getByText("Activity"));
    expect(mockNavigate).toHaveBeenCalledWith("/activity");
    expect(screen.queryByTestId("command-dialog")).not.toBeInTheDocument();
  });

  it("POSTs and closes when a Control item is selected", async () => {
    renderPalette();
    await userEvent.keyboard("{Meta>}k{/Meta}");
    await userEvent.click(screen.getByText("DND on"));
    expect(mockPost).toHaveBeenCalledWith("/api/admin/dnd", { active: true });
    expect(screen.queryByTestId("command-dialog")).not.toBeInTheDocument();
  });
});
