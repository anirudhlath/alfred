import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";

/**
 * The half-typed message, kept for the page's session so that a reload does not
 * take it — the update note's `Reload` above all, whose "Nothing is lost; I pick
 * up where we left off." would otherwise be false for whatever was in the field.
 * `sessionStorage`, not `localStorage` like `alfred.unsent`: a queued message has
 * been sent as far as the reader is concerned and must survive a cold launch, a
 * draft has not and need not outlive the tab.
 */
const DRAFT_KEY = "alfred.draft";

function readDraft(): string {
  try {
    return sessionStorage.getItem(DRAFT_KEY) ?? "";
  } catch {
    // Unavailable storage loses the draft across a reload, never the app.
    return "";
  }
}

function keepDraft(draft: string): void {
  try {
    if (draft) sessionStorage.setItem(DRAFT_KEY, draft);
    else sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    // Private mode or a full quota. The draft lives in this mount only.
  }
}

export interface ComposerProps {
  online: boolean;
  onSend: (text: string) => void;
  /** The hold-to-talk button, shown whenever there is no draft. */
  hold: ReactNode;
  /** The Workshop handle, under the row — and gone while the keyboard is up (handoff). */
  handle?: ReactNode;
}

/**
 * 50 px field, 56 px action. Never disabled offline: a message typed while the
 * house is unreachable is queued and retried, and a dead input would hide that.
 */
export function Composer({ online, onSend, hold, handle }: ComposerProps) {
  const [draft, setDraft] = useState(readDraft);
  const field = useRef<HTMLInputElement>(null);

  function changeDraft(next: string): void {
    setDraft(next);
    keepDraft(next);
  }
  /**
   * The field's own focus, not a measured keyboard. The shell is already the
   * visible band (lib/viewport.ts), so nothing here has to *position* around
   * the keys; this only decides whether the handle is in the way and whether
   * the home-indicator strip still needs paying for. Focus answers both
   * exactly, and answers them on the tap rather than a keyboard animation
   * later. A hardware keyboard makes it a shade eager — the handle goes on
   * focus with no keys coming up — which costs an affordance, not a layout.
   */
  const [focused, setFocused] = useState(false);
  const hasDraft = draft.trim().length > 0;

  function send(): void {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    changeDraft("");
    // The send button and the hold slot share one DOM node, so a click on
    // "Send" would otherwise leave focus on "hold to talk" — and drop the
    // keyboard. The next message starts in the field, like the last one did.
    field.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    // Enter while composing confirms the candidate on a CJK or predictive
    // keyboard; it is not a send, and the half-composed draft must not go out.
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      send();
    }
  }

  return (
    // Two elements on purpose: the outer one owns the keyboard and safe-area
    // padding (a Tailwind padding utility on the same element would out-rank the
    // `@layer components` rule and silently drop the inset), the inner one the
    // handoff's own `0 20 8`. The handle sits between them so the safe-area
    // inset falls below it, not between it and the row.
    <div className={`pb-keyboard relative z-[1] ${focused ? "keyboard-up" : ""}`}>
      <div className="flex items-center gap-2.5 px-5 pb-2">
        <input
          ref={field}
          value={draft}
          onChange={(event) => changeDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={online ? "Ask or tell Alfred" : "Offline · will send when connected"}
          aria-label="Message Alfred"
          autoComplete="off"
          autoCapitalize="sentences"
          autoCorrect="on"
          enterKeyHint="send"
          // 16 px, not the handoff's 15: iOS zooms the page in on any field it
          // focuses below 16, and a zoom shrinks the visual viewport that the
          // whole shell is now sized from (lib/viewport.ts) — the app would
          // shrink to the zoomed band. The one px is the cheaper of the two
          // defences (index.html locks the scale) and the only one that needs
          // no browser to co-operate.
          className="h-[50px] min-w-0 flex-1 rounded-[25px] border px-[18px] text-[16px]"
          style={{ background: "var(--field)", borderColor: "var(--line)", color: "var(--fg)" }}
        />

        {hasDraft ? (
          <button
            type="button"
            onClick={send}
            aria-label="Send"
            className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[28px] border-0"
            style={{ background: "var(--ink)", color: "var(--paper)" }}
          >
            <span
              aria-hidden="true"
              className="block h-2.5 w-2.5 border-t-2 border-r-2 border-current"
              style={{ transform: "rotate(-45deg) translate(-1px, 1px)" }}
            />
          </button>
        ) : (
          hold
        )}
      </div>
      {focused ? null : handle}
    </div>
  );
}
