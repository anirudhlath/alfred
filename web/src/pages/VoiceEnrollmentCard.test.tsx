import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { VoiceEnrollmentCard } from "./VoiceEnrollmentCard";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/chat/VoiceButton", () => ({
  VoiceButton: ({ onAudio }: { onAudio: (d: string) => void }) => (
    <button data-testid="mock-mic" onClick={() => onAudio("data:audio/webm;base64,QUJD")}>
      mic
    </button>
  ),
}));

const apiMock = vi.fn();
vi.mock("@/lib/api", () => ({ api: (...args: unknown[]) => apiMock(...args) }));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("VoiceEnrollmentCard", () => {
  it("collects three samples then enrolls", async () => {
    apiMock.mockResolvedValue({ status: "enrolled", identity: "sir" });
    render(<VoiceEnrollmentCard />);
    expect(screen.getByText(/0 \/ 3/)).toBeInTheDocument();

    const mic = screen.getByTestId("mock-mic");
    await userEvent.click(mic);
    expect(await screen.findByText(/1 \/ 3/)).toBeInTheDocument();
    await userEvent.click(mic);
    await userEvent.click(mic);

    expect(await screen.findByText(/enrolled/i)).toBeInTheDocument();
    expect(apiMock).toHaveBeenCalledWith(
      "/api/voice/enroll",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          identity: "sir",
          samples: [
            "data:audio/webm;base64,QUJD",
            "data:audio/webm;base64,QUJD",
            "data:audio/webm;base64,QUJD",
          ],
        }),
      }),
    );
  });

  it("shows an error and resets the count when enrollment fails", async () => {
    apiMock.mockRejectedValue(new Error("boom"));
    render(<VoiceEnrollmentCard />);

    const mic = screen.getByTestId("mock-mic");
    await userEvent.click(mic);
    await userEvent.click(mic);
    await userEvent.click(mic);

    expect(await screen.findByText(/enrollment failed/i)).toBeInTheDocument();
    expect(screen.getByText(/0 \/ 3/)).toBeInTheDocument();
  });
});
