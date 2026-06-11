import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { IconRail } from "./IconRail";

vi.mock("./AlfredProvider", () => ({
  useAlfred: () => ({ telemetryStatus: "online" }),
  AlfredProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

function renderRail(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <IconRail />
    </MemoryRouter>,
  );
}

describe("IconRail", () => {
  it("renders all five primary nav items plus Settings", () => {
    renderRail();
    expect(screen.getByTitle("Chat")).toBeInTheDocument();
    expect(screen.getByTitle("Activity")).toBeInTheDocument();
    expect(screen.getByTitle("Memory")).toBeInTheDocument();
    expect(screen.getByTitle("Triggers")).toBeInTheDocument();
    expect(screen.getByTitle("Health")).toBeInTheDocument();
    expect(screen.getByTitle("Settings")).toBeInTheDocument();
  });

  it("shows the pulse dot on Activity when telemetryStatus is online", () => {
    renderRail();
    const activityLink = screen.getByTitle("Activity");
    // pulse dot should be inside the Activity link
    const dot = activityLink.querySelector(".pulse-dot");
    expect(dot).toBeInTheDocument();
  });

  it("applies active styling to the current route link", () => {
    renderRail("/activity");
    const activityLink = screen.getByTitle("Activity");
    expect(activityLink).toHaveAttribute("aria-current", "page");
  });

  it("does not apply active styling to non-current route links", () => {
    renderRail("/activity");
    const chatLink = screen.getByTitle("Chat");
    expect(chatLink).not.toHaveAttribute("aria-current", "page");
  });
});
