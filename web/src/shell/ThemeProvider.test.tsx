import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY } from "@/lib/theme";
import { ThemeProvider, useTheme } from "./ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";

function Probe() {
  const { theme } = useTheme();
  return <span data-testid="theme">{theme}</span>;
}

afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("ThemeProvider", () => {
  it("applies the stored theme to the document on mount", () => {
    localStorage.setItem(THEME_KEY, "light");
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("falls back to the time of day when nothing is stored, and does not remember it", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 8, 7, 23, 0));
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    // Remembering the fallback would make tonight's dark tomorrow's noon.
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("is light by day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 8, 7, 12, 0));
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("toggles, re-applies and persists", async () => {
    const user = userEvent.setup();
    localStorage.setItem(THEME_KEY, "dark");
    render(
      <ThemeProvider>
        <ThemeToggle />
        <Probe />
      </ThemeProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Switch theme" }));

    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("refuses to be used outside the provider", () => {
    expect(() => render(<Probe />)).toThrow("useTheme outside ThemeProvider");
  });
});

describe("ThemeToggle", () => {
  it("is a 44x44 button labelled for screen readers", () => {
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
    const button = screen.getByRole("button", { name: "Switch theme" });
    expect(button).toHaveClass("h-11", "w-11");
  });
});
