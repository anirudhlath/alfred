# Conscious Engine Recalls Reflex Action History During Context Assembly

**Feature:** D8 — System 2 awareness of System 1 actions via episodic memory
**Priority:** high
**Type:** e2e

## Prerequisites
- Alfred unified runner started with all services: Reflex Engine, Memory Ingestor, Conscious Engine, Web Channel
- Redis Stack running
- At least 5 reflex observations already ingested into episodic memory (run reflex-observation-episodic-memory-end-to-end test first, or seed manually)
- Web UI accessible at `http://localhost:8081`

## Test Steps
1. Trigger several real reflex actions via Home Assistant state changes and confirm they are in episodic memory (source=reflex entries visible in Redis)
2. Open the Alfred Web UI and start a new conversation session
3. Ask Alfred a question that should recall recent reflex activity, for example:
   - "What have you done automatically in the last few hours?"
   - "Did you turn off any lights recently?"
   - "What triggered you to act on the living room lights?"
4. Observe Alfred's response and verify it references specific reflex actions (tool name, entity, outcome)
5. Ask a follow-up question: "Was that a good decision?" — verify Alfred can reason about the reflex action's context
6. Check the Conscious Engine logs for evidence that episodic memory search retrieved `source=reflex` entries during context assembly (look for involuntary recall results containing reflex summaries)

## Expected Result
- Alfred's response accurately describes the reflex actions that were taken, including the entity involved and the action outcome
- The response is grounded in actual episodic entries — not hallucinated
- Alfred can reason about whether the reflex action was appropriate given its context
- Reflex actions from `state_change` and `trigger_fired` origins are both recallable
- The episodic recall does not surface sensitive or irrelevant entries alongside reflex ones

## Notes
- This is the core System 2 ↔ System 1 feedback loop — the main user-visible benefit of D8
- The quality of recall depends on the embedding model (Gemma-300M via sentence-transformers) and the semantic key built by `_build_semantic_key()` in `core/memory/ingestor.py`
- If the embedding model is not warmed up at startup, the first few vector searches may be slow — this is a known limitation tracked in the backlog
- Test with both `origin: state_change` and `origin: trigger_fired` observations to verify both paths surface correctly in conversation
- Edge case: ask about a reflex action that failed (`result.status != "ok"`) — verify Alfred reports the failure rather than fabricating a success
