# EARS Cheat-Sheet — Easy Approach to Requirements Syntax

**SP-25 asset.** Reference template for writing well-formed acceptance criteria
in TaskSpec `acceptance_criteria[]` fields.

---

## Core Patterns

### Ubiquitous (always-on)
```
The <system> shall <system response>.
```
*Use for: invariants that hold unconditionally.*

Example: `The API shall return a JSON body for every response.`

---

### State-Driven (precondition)
```
While <precondition>, the <system> shall <system response>.
```
*Use for: behaviours active in a specific state.*

Example: `While the graph is in RUNNING state, the system shall reject
new task dispatch requests with HTTP 409.`

---

### Event-Driven (trigger)
```
When [optional precondition] <trigger event>, the <system> shall <system response>.
```
*Use for: reactions to a specific event.*

Example: `When a CI workflow run completes with conclusion=failure,
the system shall dispatch a repository_dispatch event of type ci-failure.`

---

### Unwanted Behaviour (guard)
```
If [optional precondition] <trigger>, then the <system> shall <system response>.
```
*Use for: error handling and exception paths.*

Example: `If a judge rating is missing for a gold-set item, then the
JudgeCalibrationMonitor shall raise ValueError citing the missing item_ids.`

---

### Optional Feature
```
Where <feature is included>, the <system> shall <system response>.
```
*Use for: conditionally-enabled capabilities.*

Example: `Where SPINE_BUDGET_USD is set and greater than zero, the system
shall halt graph execution before dispatching a fan-out wave whose projected
spend would exceed the cap.`

---

### Complex (state + event)
```
While <precondition>, when [precondition] <trigger>,
the <system> shall <system response>.
```
*Use for: behaviours that require both an active state and a triggering event.*

Example: `While the graph checkpoint is at an interrupt(), when the operator
sends /approve via Telegram, the system shall resume the graph on the
approved branch and clear the thread from the /pending list.`

---

## Writing Tips

1. **One requirement per sentence.** Compound sentences hide scope.
2. **Use active voice.** "The system shall validate" not "validation will occur."
3. **Avoid ambiguous modals.** "shall" = mandatory; "should" = recommended;
   "may" = optional. In acceptance criteria, use "shall" only.
4. **Name the system explicitly.** "The scrubber shall …" not "It shall …"
5. **Include the trigger.** State-driven and event-driven patterns need a
   concrete precondition or trigger, not "sometimes" or "under certain conditions."
6. **Bound quantities.** "≤5 questions" not "a small number of questions."
   "within 30 seconds" not "quickly."

---

## Acceptance Criteria Checklist

Before finalising a criterion in a TaskSpec:

- [ ] Uses EARS pattern (ubiquitous / state / event / unwanted / optional / complex)
- [ ] Exactly one verifiable claim per line
- [ ] Quantified bounds for timing, count, size
- [ ] Named system component (not "it" or "the agent")
- [ ] RED-testable — a test can be written that fails without the feature
