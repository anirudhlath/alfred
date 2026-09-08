import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StepList } from "./StepList";

describe("StepList — progress", () => {
  it("marks each row done, current or ahead", () => {
    render(
      <StepList
        variant="progress"
        steps={[
          { label: "Register this iPhone", meta: "07:02", state: "done" },
          { label: "Connect Home Assistant", state: "current" },
          { label: "Choose what the reflex may touch", state: "todo" },
        ]}
      />,
    );

    expect(screen.getByText("Register this iPhone").closest("[data-step-state]")).toHaveAttribute(
      "data-step-state",
      "done",
    );
    expect(screen.getByText("Connect Home Assistant").closest("[data-step-state]")).toHaveAttribute(
      "data-step-state",
      "current",
    );
    expect(
      screen.getByText("Choose what the reflex may touch").closest("[data-step-state]"),
    ).toHaveAttribute("data-step-state", "todo");
    expect(screen.getByText("07:02")).toBeInTheDocument();
  });

  it("renders no buttons — a progress rail is not tappable", () => {
    render(
      <StepList variant="progress" steps={[{ label: "Register this iPhone", state: "current" }]} />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("StepList — toggle", () => {
  it("shows the state word for each row", () => {
    render(
      <StepList
        variant="toggle"
        onToggle={() => {}}
        steps={[
          { id: "light", label: "Light · 6 found", allowed: true },
          { id: "fan", label: "Fan · 4 found", allowed: false },
        ]}
      />,
    );

    const light = screen.getByRole("button", { name: /Light · 6 found/ });
    const fan = screen.getByRole("button", { name: /Fan · 4 found/ });
    expect(light).toHaveTextContent("allowed");
    expect(fan).toHaveTextContent("ask me");
    expect(light).toHaveAttribute("aria-pressed", "true");
    expect(fan).toHaveAttribute("aria-pressed", "false");
  });

  it("reports the row that was tapped", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(
      <StepList
        variant="toggle"
        onToggle={onToggle}
        steps={[{ id: "media_player", label: "Media player · 2 found", allowed: true }]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Media player · 2 found/ }));

    expect(onToggle).toHaveBeenCalledWith("media_player");
  });
});
