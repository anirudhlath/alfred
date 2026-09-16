import {
  coldRow,
  hotRow,
  MEMORY_AT,
  MEMORY_RECALLED_AT,
  routine,
  searchRow,
  semanticFile,
} from "@/test/fixtures";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  episodicMeta,
  fetchEpisodic,
  fetchRoutines,
  fetchScratchpad,
  fetchSemantic,
  ROUTINE_STAGES,
  routineDetail,
  routineTrend,
  toEpisodicRow,
} from "./memory";

const AT_ISO = new Date(MEMORY_AT).toISOString();
const RECALLED_AT_ISO = new Date(MEMORY_RECALLED_AT).toISOString();

/**
 * The three shapes `GET /api/admin/memory/episodic` answers in, written out
 * literally rather than built by a helper: that they differ this much for one
 * drawn row is the reason `toEpisodicRow` exists. `the shared fixtures` below
 * pins `hotRow`/`coldRow`/`searchRow` to them, so the copies Tasks 2 and 3
 * import cannot drift away from the shapes asserted here.
 */

/** Browse · hot — a `CONTEXT_PREFIX` Redis hash, so every value is a string. */
const HOT = {
  type: "episodic",
  store: "hot",
  content: "Kitchen lamp turned off",
  semantic_key: "kitchen lamp",
  source: "system1_action",
  entities: "lamp,kitchen",
  timestamp: String(MEMORY_AT / 1000),
  significance: "0.7",
  retrieval_count: "3",
  last_retrieved: "0",
  compressed: "",
};

/** Browse · cold — a SQLite row, whose `significance` column holds JSON text. */
const COLD = {
  store: "cold",
  id: "ep-91",
  timestamp: MEMORY_AT / 1000,
  source: "conversation",
  summary: "Asked about the dentist",
  entities: '["dentist"]',
  valence: "neutral",
  significance:
    '{"overall": 0.4, "safety": 0.0, "novelty": 0.0,' +
    ' "personal": 0.0, "emotional": 0.0, "source": "heuristic"}',
  semantic_key: "dentist",
  compressed_into: null,
};

/** Search — `EpisodicEntry.model_dump(mode="json")`, so `significance` is an object. */
const FOUND = {
  store: "cold",
  score: 0.62,
  id: "ep-91",
  timestamp: AT_ISO,
  source: "conversation",
  summary: "Asked about the dentist",
  entities: ["dentist"],
  significance: {
    overall: 0.4,
    safety: 0,
    novelty: 0,
    personal: 0,
    emotional: 0,
    source: "heuristic",
  },
  semantic_key: "dentist",
  retrieval_count: 2,
  last_retrieved: RECALLED_AT_ISO,
  compressed_into: null,
  valence: "neutral",
};

const respond = (body: unknown, status = 200) =>
  vi.fn<typeof fetch>(async () => new Response(JSON.stringify(body), { status }));

describe("the shared fixtures", () => {
  it("still mirror the three shapes this file spells out", () => {
    expect(hotRow()).toEqual(HOT);
    expect(coldRow()).toEqual(COLD);
    expect(searchRow()).toEqual(FOUND);
  });
});

describe("toEpisodicRow", () => {
  it("reads a hot row's content, comma entities and string numbers", () => {
    const row = toEpisodicRow(HOT, 0);
    expect(row).toMatchObject({
      store: "hot",
      text: "Kitchen lamp turned off",
      at: MEMORY_AT,
      significance: 0.7,
      recalled: 3,
      entities: ["lamp", "kitchen"],
      score: null,
    });
  });

  it("gives a hot row an index key, because the server discards its id", () => {
    expect(toEpisodicRow(HOT, 4)).toMatchObject({ key: "hot:4", id: null });
  });

  it("treats a blank id as no id rather than as an empty React key", () => {
    expect(toEpisodicRow({ ...COLD, id: "" }, 3)).toMatchObject({ key: "cold:3", id: null });
  });

  it("reads a cold row's summary and JSON entities", () => {
    const row = toEpisodicRow(COLD, 0);
    expect(row).toMatchObject({
      store: "cold",
      id: "ep-91",
      key: "ep-91",
      text: "Asked about the dentist",
      at: MEMORY_AT,
      significance: 0.4,
      recalled: 0,
      entities: ["dentist"],
    });
  });

  it("reads a search row's ISO timestamp, array entities and score", () => {
    const row = toEpisodicRow(FOUND, 0);
    expect(row).toMatchObject({
      at: MEMORY_AT,
      entities: ["dentist"],
      score: 0.62,
      recalled: 2,
      lastRecalled: MEMORY_RECALLED_AT,
    });
  });

  it("reads the significance each store spells differently, and a bare number besides", () => {
    // A string float (the hot hash), JSON text (the cold store's TEXT column), a
    // dumped SignificanceScore (search) — and a bare number, defensively.
    expect(toEpisodicRow({ ...COLD, significance: "0.4" }, 0).significance).toBe(0.4);
    expect(toEpisodicRow({ ...COLD, significance: '{"overall": 0.4}' }, 0).significance).toBe(0.4);
    expect(toEpisodicRow({ ...COLD, significance: { overall: 0.4 } }, 0).significance).toBe(0.4);
    expect(toEpisodicRow({ ...COLD, significance: 0.4 }, 0).significance).toBe(0.4);
    expect(toEpisodicRow({ ...COLD, significance: "not a score" }, 0).significance).toBeNull();
  });

  it("treats a zero epoch as never, not as 1970", () => {
    // The hot hash writes `last_retrieved: 0.0` for a memory nothing has recalled.
    expect(toEpisodicRow(HOT, 0).lastRecalled).toBeNull();
    expect(toEpisodicRow({ ...HOT, timestamp: "0" }, 0).at).toBeNull();
  });

  it("reads an empty string as no value rather than as zero", () => {
    const row = toEpisodicRow({ ...HOT, timestamp: "", significance: "", retrieval_count: "" }, 0);
    expect(row).toMatchObject({ at: null, significance: null, recalled: 0 });
  });

  it("keeps the recall count a whole number that cannot go below none", () => {
    expect(toEpisodicRow({ ...HOT, retrieval_count: "2.7" }, 0).recalled).toBe(2);
    expect(toEpisodicRow({ ...HOT, retrieval_count: -1 }, 0).recalled).toBe(0);
  });

  it("survives every field being missing", () => {
    const row = toEpisodicRow({ store: "hot" }, 2);
    expect(row).toEqual({
      key: "hot:2",
      id: null,
      store: "hot",
      text: "",
      at: null,
      significance: null,
      recalled: 0,
      lastRecalled: null,
      entities: [],
      score: null,
      decaying: false,
    });
  });

  it("treats an unparseable entities string as no entities, not as one entity", () => {
    expect(toEpisodicRow({ ...COLD, entities: "[oops" }, 0).entities).toEqual([]);
  });

  it("drops empty entity fragments", () => {
    expect(toEpisodicRow({ ...HOT, entities: "lamp,,kitchen," }, 0).entities).toEqual([
      "lamp",
      "kitchen",
    ]);
  });

  it("calls a cold row with low significance and no recalls decaying", () => {
    expect(toEpisodicRow({ ...COLD, significance: 0.2 }, 0).decaying).toBe(true);
    expect(toEpisodicRow({ ...COLD, significance: 0.8 }, 0).decaying).toBe(false);
    expect(toEpisodicRow({ ...FOUND, significance: 0.2 }, 0).decaying).toBe(false);
    expect(toEpisodicRow({ ...HOT, significance: "0.2" }, 0).decaying).toBe(false);
  });

  it("never calls a row it cannot read the significance of decaying", () => {
    // `null < DECAY_FLOOR` is true in JS, so dropping the guard would condemn
    // every cold row whose significance column is unreadable.
    expect(toEpisodicRow({ ...COLD, significance: "not a score" }, 0).decaying).toBe(false);
    expect(toEpisodicRow({ ...COLD, significance: undefined }, 0).decaying).toBe(false);
  });
});

describe("episodicMeta", () => {
  it("stamps, names the store and counts recalls", () => {
    expect(episodicMeta(toEpisodicRow(HOT, 0))).toBe("07:02 · hot · recalled 3×");
  });

  it("says never recalled rather than 0×", () => {
    expect(episodicMeta(toEpisodicRow(COLD, 0))).toBe("07:02 · cold · never recalled");
  });

  it("adds the match score when one is in the row", () => {
    expect(episodicMeta(toEpisodicRow(FOUND, 0))).toBe("07:02 · cold · recalled 2× · match 0.62");
  });

  it("says decaying instead of a recall count when the row is decaying", () => {
    expect(episodicMeta(toEpisodicRow({ ...COLD, significance: 0.2 }, 0))).toBe(
      "07:02 · cold · decaying",
    );
  });

  it("says --:-- for a row with no readable time", () => {
    expect(episodicMeta(toEpisodicRow({ store: "hot" }, 0))).toBe("--:-- · hot · never recalled");
  });
});

describe("ROUTINE_STAGES", () => {
  it("is the Librarian's lifecycle, in order", () => {
    expect(ROUTINE_STAGES).toEqual(["candidate", "active", "dormant", "archived"]);
  });
});

describe("routineTrend", () => {
  it("reports a rise against the previous consolidation", () => {
    // The fixture's history is 0.6 → 0.71 → 0.82, at a confidence of 0.82.
    expect(routineTrend(routine())).toEqual({
      text: "0.82 · +0.11 since last week",
      rising: true,
    });
  });

  it("reports a fall", () => {
    expect(routineTrend(routine({ confidence_history: [0.9, 0.82] }))).toEqual({
      text: "0.82 · -0.08 since last week",
      rising: false,
    });
  });

  it("says nothing about a trend it has only one reading for", () => {
    expect(routineTrend(routine({ confidence_history: [0.82] }))).toEqual({
      text: "0.82 · first reading",
      rising: false,
    });
  });

  it("treats an empty history as a first reading rather than reading confidence twice", () => {
    expect(routineTrend(routine({ confidence_history: [] })).text).toBe("0.82 · first reading");
  });

  it("does not call a routine that held still rising", () => {
    expect(routineTrend(routine({ confidence_history: [0.82, 0.82] }))).toEqual({
      text: "0.82 · +0.00 since last week",
      rising: false,
    });
  });
});

describe("routineDetail", () => {
  it("counts the steps and the evidence", () => {
    expect(routineDetail(routine({ learned_from: ["ep-1"] }))).toBe(
      "2 steps · learned from 1 memory",
    );
  });

  it("pluralises the evidence", () => {
    expect(routineDetail(routine())).toBe("2 steps · learned from 2 memories");
  });

  it("says no evidence kept when the routine has none", () => {
    expect(routineDetail(routine({ learned_from: [] }))).toBe("2 steps · no evidence kept");
  });

  it("says 1 step, not 1 steps", () => {
    expect(routineDetail(routine({ steps: [{ description: "a", action: null }] }))).toBe(
      "1 step · learned from 2 memories",
    );
  });
});

describe("fetchEpisodic", () => {
  it("browses without a q parameter", async () => {
    const fetchMock = respond({ entries: [] });
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchEpisodic("")).toEqual([]);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/admin/memory/episodic");
  });

  it("encodes the query it searches with", async () => {
    const fetchMock = respond({ entries: [] });
    vi.stubGlobal("fetch", fetchMock);

    await fetchEpisodic("dentist & co");

    const url = new URL(String(fetchMock.mock.calls[0][0]), "https://alfred.example.com");
    expect(url.pathname).toBe("/api/admin/memory/episodic");
    expect(url.searchParams.get("q")).toBe("dentist & co");
  });

  it("maps every entry through the adapter, index and all", async () => {
    vi.stubGlobal("fetch", respond({ entries: [HOT, COLD] }));

    const rows = await fetchEpisodic("");

    expect(rows.map((row) => row.key)).toEqual(["hot:0", "ep-91"]);
    expect(rows.map((row) => row.text)).toEqual([
      "Kitchen lamp turned off",
      "Asked about the dentist",
    ]);
  });

  it("reads a body with no entries as no rows", async () => {
    vi.stubGlobal("fetch", respond({}));
    expect(await fetchEpisodic("")).toEqual([]);
  });

  it("lets the embedder's 503 through, for the model pill to read", async () => {
    vi.stubGlobal("fetch", respond({ detail: "Vector search unavailable" }, 503));
    await expect(fetchEpisodic("dentist")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("the other three reads", () => {
  it("unwraps the semantic envelope", async () => {
    const file = semanticFile();
    const fetchMock = respond({ files: [file] });
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchSemantic()).toEqual([file]);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/admin/memory/semantic");
  });

  it("unwraps the routines envelope", async () => {
    const fetchMock = respond({ routines: [routine()] });
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchRoutines()).toEqual([routine()]);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/admin/memory/routines");
  });

  it("reads the scratchpad and its queue", async () => {
    const fetchMock = respond({ content: "- lamp", pending_queue: 2 });
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchScratchpad()).toEqual({ content: "- lamp", pending_queue: 2 });
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/admin/memory/scratchpad");
  });

  it("reads a scratchpad with nothing in it as empty, not as absent", async () => {
    vi.stubGlobal("fetch", respond({}));
    expect(await fetchScratchpad()).toEqual({ content: "", pending_queue: 0 });
  });
});
