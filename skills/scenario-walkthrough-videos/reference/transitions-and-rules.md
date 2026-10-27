# Transitions and rules

Each frame lists `transitions`. The engine (`scripts/engine.py`, section 1) applies them in order to the state of
the previous frame, so a frame states only what changes. Each transition is checked before it changes anything: an
unknown field, a value outside its `enum`, a null in a `nullable: false` field, a foreign key to a missing row, or a
declared rule stops the build and names the frame and the transition.

## The ops

| op | Keys | What it does | Marks on the frame |
|---|---|---|---|
| `add_row` | `entity`, `row` | Adds a row. Missing fields are null; the key must be new. | Green row, green ring on its chips, NEW tag on a lane. |
| `update_row` | `entity`, `id`, `set` | Sets several fields of one row. The key cannot change. | Yellow behind each changed cell. |
| `set_field` | `entity`, `id`, `field`, `value` | Shorthand for an `update_row` of one field. Rules on `update_row` cover it. | Yellow cell. |
| `move_row` | `entity`, `field` (a `ref` field), `to`, and `id`, `ids` or `where`; optional `set` | Points one or more rows at another row of the referenced entity (`to` may be null). `where` matches every row whose fields equal the given values. `set` changes other fields in the same step. | Yellow cell, orange ring on the moved chips. |
| `delete_row` | `entity`, `id` | Removes a row. Refused (rule `foreign-key`) while any row still points at it. | The row stays for this frame only, dashed and struck through, at its old place; a lane becomes a dashed "deleted" lane. |

Every op also takes `note` (free text, ignored) and `expect_refused` (below).

A merge is two steps: `move_row` with `where` from the old row to the kept one, then `delete_row` of the old row.
A rename is an `update_row` of the name: the key stays, so every reference stays.

## Unbuilt steps

When a frame's badge sets `"unbuilt": true` (in the example: `designed`, `planned`, `inferred`), every row that its
transitions add or change is marked. A marked row is drawn dashed and italic in the badge colour, in this frame and
every later one, until it is deleted. So a planned change can never look like a built one further on in the video.

## Rules

A rule refuses a transition when its condition holds. It is data, so the validator runs it on every step.

```json
{"id": "assign-needs-category", "op": "move_row", "entity": "requests", "field": "agent_id",
 "when": {"all": [{"field": "new.agent_id", "is_null": false},
                  {"field": "row.category_id", "is_null": true}]},
 "message": "a request with no category cannot get an agent", "sources": ["GUIDE-3"]}
```

| Key | Meaning |
|---|---|
| `op` | One op name or a list. `set_field` counts as `update_row`. |
| `entity` | The entity the transition changes. |
| `field` | Optional: the rule applies only when the transition sets this field. |
| `when` | The condition (below). |
| `message`, `sources` | Shown in the error; cite where the rule comes from. |

Scopes inside a condition: `row` is the row before the change, `new` is the row after it, `to` is the row a
`move_row` points at (null when `to` is null). For `add_row`, `row` and `new` are the new row; for `delete_row`,
only `row` exists.

| Condition | Holds when |
|---|---|
| `{"field": "new.status", "eq": "escalated"}` | The value compares true. Comparators: `eq`, `ne`, `in`, `not_in`, `gt`, `ge`, `lt`, `le`, `is_null` (true or false). Exactly one per condition. |
| `{"all": [c1, c2]}`, `{"any": [...]}`, `{"not": c}` | Logic. |
| `{"count": {"entity": "agents", "where": {"id": "$new.agent_id", "team": "Tier 2"}}, "eq": 0}` | The number of rows of an entity whose fields equal the values compares true. A value `"$scope.field"` reads a field of a scope. |

## Show a refused step

Add the transition the real system refuses, with `"expect_refused": "<rule id>"`. The engine applies it; when the
rule refuses it, the state stays as it was and the renderer draws a red dashed box and a "REFUSED: rule id" label
on the row. When the rule does not refuse it, the build fails ("went through"), so the video cannot claim a refusal
that the rules do not make. The built-in rule id for deleting a referenced row is `foreign-key`.

## Invariants the engine keeps

- The order of transitions is the order of the JSON; a later frame sees every earlier change.
- A refused transition with several target rows changes none of them (all rows are checked first).
- `compute_frames` returns a deep copy of the state after each frame, so a test can read any frame's state
  (`state_after(scenario, frame_id)`).
