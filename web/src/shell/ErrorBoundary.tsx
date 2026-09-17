import { Component, type ErrorInfo, type ReactNode } from "react";

export interface ErrorBoundaryProps {
  /** What to draw instead of the subtree that threw. */
  fallback: ReactNode;
  children: ReactNode;
}

interface ErrorBoundaryState {
  failed: boolean;
}

/**
 * The one class component in `src/`, because React has no hook for this: a
 * boundary must implement `getDerivedStateFromError`, and that is a class-only
 * lifecycle.
 *
 * Why the app needs one at all: a render that throws anywhere under the root
 * unmounts the **whole tree**, and a PWA with no tree is a white screen that
 * only a reload clears — on a phone, with no address bar in standalone mode,
 * that is a long way to go for a bad JSON body. This one is placed so that a
 * bench which throws costs the reader the Workshop and not the Room: the chat,
 * the Door and the gates are all outside it and keep working.
 *
 * It does not offer a "try again". Nothing here knows *what* failed, and a
 * button that re-renders the same bad state is a button that fails again under
 * the same finger. The way out is to leave the layer, which unmounts this
 * boundary with it, so the next open starts from a fresh mount — the reset is
 * the surface's own lifecycle rather than a second mechanism to get wrong.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The only record there is: this API has no error route, and nothing in
    // this app sends a stack anywhere. A phone plugged into a laptop can read
    // it, which is how the one that put this boundary here was found.
    console.error("A bench threw while rendering", error, info.componentStack);
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
