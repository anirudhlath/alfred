import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StreamRef } from "@/lib/streams";
import { useDetail } from "./useDetail";

const ROW: StreamRef = { stream: "reflex_observations", entry: { id: "1-0", event: {} } };
const OTHER: StreamRef = { stream: "reflex_observations", entry: { id: "2-0", event: {} } };

describe("useDetail", () => {
  it("starts with nothing open", () => {
    const { result } = renderHook(() => useDetail());
    expect(result.current.why).toBeNull();
    expect(result.current.heldOpen).toBe(false);
  });

  it("opening the thread closes Held back", () => {
    const { result } = renderHook(() => useDetail());
    act(() => result.current.openHeld());
    expect(result.current.heldOpen).toBe(true);

    act(() => result.current.openWhy(ROW));
    expect(result.current.why).toBe(ROW);
    expect(result.current.heldOpen).toBe(false);
  });

  it("opening Held back closes the thread", () => {
    const { result } = renderHook(() => useDetail());
    act(() => result.current.openWhy(ROW));

    act(() => result.current.openHeld());
    expect(result.current.heldOpen).toBe(true);
    expect(result.current.why).toBeNull();
  });

  it("re-pointing the thread keeps it open", () => {
    const { result } = renderHook(() => useDetail());
    act(() => result.current.openWhy(ROW));

    act(() => result.current.openWhy(OTHER));
    expect(result.current.why).toBe(OTHER);
    expect(result.current.heldOpen).toBe(false);
  });

  it("each close closes its own", () => {
    const { result } = renderHook(() => useDetail());
    act(() => result.current.openWhy(ROW));
    act(() => result.current.closeHeld());
    expect(result.current.why).toBe(ROW);

    act(() => result.current.closeWhy());
    expect(result.current.why).toBeNull();

    act(() => result.current.openHeld());
    act(() => result.current.closeWhy());
    expect(result.current.heldOpen).toBe(true);

    act(() => result.current.closeHeld());
    expect(result.current.heldOpen).toBe(false);
  });

  it("the callbacks keep their identity across renders", () => {
    const { result, rerender } = renderHook(() => useDetail());
    const { openWhy, openHeld, closeWhy, closeHeld } = result.current;

    act(() => result.current.openWhy(ROW));
    rerender();
    act(() => result.current.openHeld());

    expect(result.current.openWhy).toBe(openWhy);
    expect(result.current.openHeld).toBe(openHeld);
    expect(result.current.closeWhy).toBe(closeWhy);
    expect(result.current.closeHeld).toBe(closeHeld);
  });
});
