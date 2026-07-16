import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageItem } from "./MessageItem";
import type { ChatMessage } from "./use-chat";

describe("MessageItem", () => {
  it("renders a user message aligned right with secondary background", () => {
    const message: ChatMessage = { role: "user", text: "Turn off the lights" };
    const { container } = render(<MessageItem message={message} />);
    expect(screen.getByText("Turn off the lights")).toBeInTheDocument();
    // self-end positions it on the right
    const bubble = container.firstChild as HTMLElement;
    expect(bubble.className).toContain("self-end");
    expect(bubble.className).toContain("bg-secondary");
  });

  it("renders an alfred message with left border accent and no work-line when no tools or latency", () => {
    const message: ChatMessage = { role: "alfred", text: "Of course, sir." };
    const { container } = render(<MessageItem message={message} />);
    expect(screen.getByText("Of course, sir.")).toBeInTheDocument();
    const wrapper = container.firstChild as HTMLElement;
    expect(wrapper.className).toContain("self-start");
    expect(wrapper.className).toContain("border-l-2");
    // No work-line (no tools or latency)
    expect(screen.queryByText(/▸/)).not.toBeInTheDocument();
  });

  it("renders the work-line with tool names and latency when both are present", () => {
    const message: ChatMessage = {
      role: "alfred",
      text: "Lights dimmed to 40%, sir.",
      tools: ["smart_home.dim_lights", "smart_home.get_state"],
      latencyMs: 2350,
    };
    render(<MessageItem message={message} />);
    expect(screen.getByText("▸ smart_home.dim_lights")).toBeInTheDocument();
    expect(screen.getByText("▸ smart_home.get_state")).toBeInTheDocument();
    // 2350ms → 2.4s
    expect(screen.getByText("2.4s")).toBeInTheDocument();
  });

  it("renders the work-line with tools only (no latency span) when latencyMs is absent", () => {
    const message: ChatMessage = {
      role: "alfred",
      text: "Done.",
      tools: ["calendar.get_events"],
    };
    render(<MessageItem message={message} />);
    expect(screen.getByText("▸ calendar.get_events")).toBeInTheDocument();
    // No latency
    expect(screen.queryByText(/^\d+\.\d+s$/)).not.toBeInTheDocument();
  });

  it("renders the work-line with latency only (no tools) when tools array is empty", () => {
    const message: ChatMessage = {
      role: "alfred",
      text: "Here you go.",
      tools: [],
      latencyMs: 1000,
    };
    render(<MessageItem message={message} />);
    expect(screen.getByText("1.0s")).toBeInTheDocument();
    expect(screen.queryByText(/▸/)).not.toBeInTheDocument();
  });

  it("renders a system message with mono font and no work-line", () => {
    const message: ChatMessage = { role: "system", text: "Not connected — message not sent." };
    const { container } = render(<MessageItem message={message} />);
    expect(screen.getByText("Not connected — message not sent.")).toBeInTheDocument();
    const el = container.firstChild as HTMLElement;
    expect(el.className).toContain("font-mono");
    expect(el.className).toContain("self-center");
    expect(screen.queryByText(/▸/)).not.toBeInTheDocument();
  });
});
