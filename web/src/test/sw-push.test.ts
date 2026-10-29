import { describe, expect, it } from "vitest";
// The shipped bytes, as sw.test.ts reads them.
import source from "../../public/sw.js?raw";
// The same file and the kill switch, each as its exact bytes: see FIRED.
import shippedBytes from "../../public/sw.js?url&inline";
import killSwitchBytes from "../../public/sw.kill.js?url&inline";
import { type LoadOptions, loadWorker, ORIGIN } from "./sw-harness";

/**
 * Whether the kill switch is fired: sw.js a byte-for-byte copy of sw.kill.js,
 * told exactly as sw.test.ts tells it, and for the same reason. The switch
 * shows no pushes, and these tests are about the worker the copy took out, so
 * they stand down and the one test that runs says why.
 */
const FIRED = shippedBytes === killSwitchBytes;

if (FIRED) {
  it("stands down: web/public/sw.js is the kill switch, fired — sw.kill.js byte for byte", async ({
    annotate,
  }) => {
    expect(shippedBytes).toBe(killSwitchBytes);
    // As in sw.test.ts: a warning on the run, where a skipped count says nothing of why.
    await annotate(
      "The kill switch is fired: web/public/sw.js is sw.kill.js byte for byte, so sw-push.test.ts's tests of the real worker stand down until the firing commit is reverted.",
      "warning",
    );
  });
}

/**
 * What `build_payload()` (core/notifications/adapters/webpush.py) sends for a
 * notification that names an action: these five keys, and no others.
 */
const APPROVAL = {
  title: "Unlock the front door?",
  body: "The courier is at the door.",
  urgency: "urgent",
  id: "n-42",
  action_id: "abc-123",
};

/** And for one that names none: `action_id` is null. */
const NOTICE = {
  title: "Bins go out tonight",
  body: "The council moved collection to Friday.",
  urgency: "important",
  id: "n-7",
  action_id: null,
};

/** What a push the worker cannot read is shown as. */
const FALLBACK = { title: "Alfred", options: { body: "", data: { path: "/" } } };

/** The path the worker gave the one notification it showed. */
const pathOf = (shown: { options: NotificationOptions }[]): unknown => {
  expect(shown).toHaveLength(1);
  return (shown[0].options.data as { path: unknown }).path;
};

describe.skipIf(FIRED)("push", () => {
  it("shows the house's title and body, tagged by id", async () => {
    // Tagged so that a second push about the same notification replaces the
    // first on the lock screen rather than stacking beside it.
    const worker = loadWorker(source);
    await worker.push(NOTICE);
    expect(worker.shown).toEqual([
      { title: NOTICE.title, options: { body: NOTICE.body, tag: "n-7", data: { path: "/" } } },
    ]);
  });

  it("deep-links a notification that names an action", async () => {
    const worker = loadWorker(source);
    await worker.push(APPROVAL);
    expect(pathOf(worker.shown)).toBe("/actions/abc-123");
  });

  it("links home for an action id it would not route", async () => {
    // The worker builds the link and the payload only names an id, so nothing
    // the server sends — or a relay that tampered with it could — names a URL
    // this origin opens. Each is a way past a check that only looked for a
    // slash, or only at the length, or at what a number coerces to — or at
    // letters outside ASCII, or a pattern whose `$` let a trailing newline by.
    const refused = ["../x", "a/b", "x".repeat(129), "", "abc 123", "abç", "abc\n", 123, ["abc"], { id: "abc" }];
    for (const actionId of refused) {
      const worker = loadWorker(source);
      await worker.push({ ...APPROVAL, action_id: actionId });
      expect(pathOf(worker.shown), String(actionId)).toBe("/");
    }
    // And the boundary from the other side: 128 is routed, so 129 was refused
    // for its length and not for its characters.
    const worker = loadWorker(source);
    await worker.push({ ...APPROVAL, action_id: "x".repeat(128) });
    expect(pathOf(worker.shown)).toBe(`/actions/${"x".repeat(128)}`);
  });

  it("shows a notification for a push with no data", async () => {
    // A push the worker shows nothing for is the one failure that costs more
    // than itself: iOS revokes a subscription that receives pushes it does not
    // show, and Chrome shows "This site has been updated in the background".
    const worker = loadWorker(source);
    await worker.push();
    expect(worker.shown).toEqual([FALLBACK]);
    expect(worker.shown[0].options).not.toHaveProperty("tag");
  });

  it("shows a notification for a push that is not JSON", async () => {
    const worker = loadWorker(source);
    await worker.push(undefined, { raw: "Unlock the front door?" });
    expect(worker.shown).toEqual([FALLBACK]);
  });

  it("shows a notification for JSON with no title", async () => {
    // A title that is missing, empty or not a string, and JSON that is not an
    // object at all — null, a string, a number, an array — each has no title.
    const worker = loadWorker(source);
    await worker.push({ body: "The courier is at the door.", id: "n-1" });
    expect(worker.shown).toEqual([
      { title: "Alfred", options: { body: "The courier is at the door.", tag: "n-1", data: { path: "/" } } },
    ]);
    for (const payload of [{ ...NOTICE, title: "" }, { ...NOTICE, title: 42 }, null, "Bins", 7, [NOTICE]]) {
      const other = loadWorker(source);
      await other.push(payload);
      expect(other.shown[0]?.title, JSON.stringify(payload)).toBe("Alfred");
    }
  });

  it("shows an empty body, and no tag, for a body or an id that is not a string", async () => {
    const worker = loadWorker(source);
    await worker.push({ ...NOTICE, body: 42, id: 7 });
    expect(worker.shown).toEqual([{ title: NOTICE.title, options: { body: "", data: { path: "/" } } }]);
    expect(worker.shown[0].options).not.toHaveProperty("tag");
  });

  it("waits for showNotification inside waitUntil", async () => {
    // The harness's showNotification lands a macrotask after it is called, so
    // nothing is shown yet when the listener returns; a worker that did not
    // extend the event with it would have the event over first, and a browser
    // free to stop it with nothing on the lock screen.
    const worker = loadWorker(source);
    const pushed = worker.push(APPROVAL);
    expect(worker.shown).toEqual([]);
    await pushed;
    expect(worker.shown).toHaveLength(1);
  });
});

describe.skipIf(FIRED)("notificationclick", () => {
  /** A phone with the app open in a page this worker controls, at `path`. */
  const controlling = async (path: string) => {
    const options: LoadOptions = { update: true, clients: [{ path, controlled: true }] };
    const worker = loadWorker(source, options);
    // Activation is what makes the older worker's pages this one's.
    await worker.activate();
    return worker;
  };

  const TAPPED = { path: "/actions/abc-123" };

  it("focuses an open window and posts it the path", async () => {
    // Posted rather than navigated: the page routes it itself, so a tap lands
    // in the app the owner already has open, conversation and all, instead of
    // reloading it.
    const worker = await controlling("/workshop");
    await worker.notificationClick(TAPPED);
    expect(worker.focused).toEqual([`${ORIGIN}/workshop`]);
    expect(worker.posted).toEqual([
      { url: `${ORIGIN}/workshop`, data: { type: "OPEN_PATH", path: "/actions/abc-123" } },
    ]);
    expect(worker.opened).toEqual([]);
  });

  it("focuses the window before it posts the path", async () => {
    // A browser lets a worker focus a window only in answer to a tap, so the
    // focus goes first, and the path arrives in a window already in front.
    const worker = loadWorker(source);
    await worker.notificationClick(TAPPED);
    expect(worker.events).toEqual([`focus ${ORIGIN}/`, `post ${ORIGIN}/`]);
  });

  it("asks matchAll() for windows, the uncontrolled ones included", async () => {
    // Windows, because "all" lists worker clients too, which have no focus():
    // the first client on this origin could be one.
    const worker = loadWorker(source);
    await worker.notificationClick(TAPPED);
    expect(worker.matchAllOptions).toEqual([{ type: "window", includeUncontrolled: true }]);
  });

  it("posts the path to an uncontrolled window", async () => {
    // The page that registered this worker is controlled by no worker until it
    // reloads, and it is the app all the same: matchAll() lists it only with
    // includeUncontrolled.
    const worker = loadWorker(source);
    await worker.notificationClick(TAPPED);
    expect(worker.focused).toEqual([`${ORIGIN}/`]);
    expect(worker.posted).toEqual([{ url: `${ORIGIN}/`, data: { type: "OPEN_PATH", path: "/actions/abc-123" } }]);
    expect(worker.opened).toEqual([]);
  });

  it("posts even when focus is refused", async () => {
    // A refused focus leaves the window where it was, and the path still
    // belongs in it rather than in a second copy of the app opened beside it.
    const worker = loadWorker(source);
    worker.refuseFocus("/");
    await worker.notificationClick(TAPPED);
    expect(worker.focused).toEqual([]);
    expect(worker.posted).toEqual([{ url: `${ORIGIN}/`, data: { type: "OPEN_PATH", path: "/actions/abc-123" } }]);
    expect(worker.opened).toEqual([]);
  });

  it("passes over a window that is not on this origin", async () => {
    // A window that has not loaded anything yet can be about:blank, whose
    // origin is "null", and nothing there listens for OPEN_PATH.
    const worker = loadWorker(source, { clients: [{ path: "about:blank" }] });
    await worker.notificationClick(TAPPED);
    expect(worker.posted).toEqual([]);
    expect(worker.opened).toEqual([`${ORIGIN}/actions/abc-123`]);
  });

  it("opens a window when none is open", async () => {
    const worker = loadWorker(source, { clients: [] });
    await worker.notificationClick(TAPPED);
    expect(worker.opened).toEqual([`${ORIGIN}/actions/abc-123`]);
    expect(worker.focused).toEqual([]);
    expect(worker.posted).toEqual([]);
  });

  it("opens home for a path it did not build", async () => {
    // A notification's data is whatever its showNotification was handed, and
    // this worker builds exactly two shapes of path. Anything else — another
    // host, a protocol-relative URL, a route it does not link to, the prefix
    // without its slash, a traversal, no path at all — is not one of its own,
    // and goes home.
    const foreign = [
      { path: "https://203.0.113.9/" },
      { path: "//203.0.113.9/actions/abc-123" },
      { path: "/workshop" },
      { path: "/actionsabc-123" },
      { path: "/actions/../api/push/public-key" },
      { path: "/actions/a/b" },
      { path: `/actions/${"x".repeat(129)}` },
      { path: "/actions/abç" },
      { path: "/actions/abc\n" },
      { path: 7 },
      "/actions/abc-123",
      null,
    ];
    const worker = loadWorker(source, { clients: [] });
    for (const data of foreign) await worker.notificationClick(data);
    expect(worker.opened).toEqual(foreign.map(() => `${ORIGIN}/`));
    // The same on the other branch: a window is posted home, too.
    const open = loadWorker(source);
    await open.notificationClick({ path: "https://203.0.113.9/" });
    expect(open.posted).toEqual([{ url: `${ORIGIN}/`, data: { type: "OPEN_PATH", path: "/" } }]);
  });

  it("closes the notification it was tapped on", async () => {
    // Chrome does not close a tapped notification itself: one the worker
    // leaves open stays in the shade, and a second tap opens it again.
    const worker = loadWorker(source);
    await worker.notificationClick(TAPPED);
    expect(worker.closedNotifications).toBe(1);
  });
});

describe.skipIf(FIRED)("the push listeners and the network", () => {
  it("still never answers /api/* (rule 1 is unchanged)", async () => {
    // The key a page subscribes with is the newest /api/ route the worker
    // could be tempted to keep for an offline launch, and it is still the
    // server's to state.
    const worker = loadWorker(source);
    await expect(worker.request({ path: "/api/push/public-key", mode: "cors" })).resolves.toBeUndefined();
    expect(worker.fetched).toEqual([]);
    expect(worker.puts).toEqual([]);
  });
});
