---
name: task-tracker
description: >
  Keep the running task list in one Markdown file, `tasks-record.md` at the project root: a prioritized TODO list, Notes on the
  tasks, and a Done list newest first. Use when the user asks to add, reprioritize, update, complete or list a
  task ("add a task", "put this on the TODO list", "mark X done", "what is left to do"), when they ask what the
  open follow-ups are, and when Claude has a follow-up worth tracking (then ask the user before adding it).
model: sonnet
effort: medium
---

# task-tracker

This skill is set to run with Sonnet at medium effort (the `model` and `effort` settings above); it needs no more.

One Markdown file holds every task we plan to follow up:

`tasks-record.md` in the root of the current project. If the project already keeps its task list in another
file, or the user names one, use that file instead and say so once. If the file does not exist, create it with the
three empty sections below.

Read the whole file before each change, and edit it in place. Never regenerate it from memory.

## The file layout

The sections come in this order. More sections can be added as needed, below `Done` unless the user says
otherwise.

```markdown
# Tasks record

## TODO

1. **[T-007] Convert and summarize Devroye, A Probabilistic Theory of Pattern Recognition** - added 2026-10-09.
   Vision mode, about 1.5 h of local time. See note N-002.
2. **[T-004] ...** - added 2026-10-08.

## Notes

- **N-002 (T-007):** The PDF is scanned with an invisible CVISION OCR layer; pdf-to-markdown vision mode keeps
  the vision reading of failed blocks (fixed 2026-10-08).
- **N-001 (T-003, T-005):** ...

## Done

- **[T-003] ...** - done 2026-10-09 (completed). Commit 458cd89. One line on the outcome.
- **[T-001] ...** - done 2026-10-07 (agreed). ...
```

### TODO

- A numbered list in priority order: the highest priority is item 1, at the top.
- Each task has a stable ID `T-NNN`. Take the next number after the highest ID anywhere in the file (TODO or
  Done). IDs never change and are never reused, so notes and commits can point to them while the list order
  changes.
- Each entry: the ID and a short bold title, the date it was added, then one to three lines on what is to be done
  and any pointer (file path, note ID).
- Cost and size, only when the task has an estimate: the last line of the entry, after a blank line, indented to
  the text of the entry's first line (3 spaces for items 1 to 9, 4 for items 10 to 99, and the same for the entry's other
  continuation lines, so the line stays inside the list item when the file is rendered), in bold, in exactly this form:

  ```markdown
     **Cost: about $12 ($10-15) | Size: 0.26 MB in scope, file 0.30 MB**
  ```

  The cost is `about $X` with the range in brackets (`about $4` when there is no range). The size is what drives
  the cost: MB in scope and the file size for a book, words for a summary or a narrative, or a count of files. Say
  the same thing once: do not repeat the estimate in the entry's text. A task with no estimate has no such line,
  and no placeholder. When the estimate changes, edit this line, and say in a note why.
- A new task goes where the user puts it. With no position given, ask, or put it at the bottom and say so.
- On a priority change, move the entry and renumber the list. The IDs stay the same.

### Notes

- Important information about one or more tasks: decisions, constraints, findings, links, partial progress.
- Each note has a stable ID `N-NNN` and lists the task IDs it relates to: `**N-004 (T-002, T-006):**`.
- Newest note at the top. Edit a note when the facts change; keep it short and dated where the date matters.
- A note whose tasks are all done stays (it is the record of why), unless the user asks to remove it.

### Done

- Completed tasks, ordered by completion date with the most recent at the top. A task without a date goes below
  the dated ones.
- Moving a task: remove it from TODO (renumber TODO), and add it to Done with its ID, title, completion date, the
  basis of that date, and one line on the outcome (commit hash, output file).
- The completion date is usually the day the task was completed. Sometimes it is the day it is moved to Done, and
  sometimes a date the user and Claude decide on. Write the basis after the date:
  - `(completed)`: the day the work was finished;
  - `(moved)`: the day it was moved to Done, when the finish date is not known;
  - `(agreed)`: a date the user chose or we agreed on.
  When it is not clear which applies, ask the user.
- Dates are absolute, `YYYY-MM-DD`. Convert "yesterday" or "last week" to a date.

## When a task is added

- **The user asks.** Add it as asked, with the wording, priority and date they give.
- **Claude proposes.** When work leaves a follow-up worth doing (an offer the user did not answer, a known gap, a
  defect to fix later, a deferred decision), ask the user whether to add it, with a proposed title and position.
  Add it only after they agree. Do not add tasks on Claude's own judgment.
- Before adding, check that the task is not already in TODO or Done; update the existing entry instead.

## When a task is completed

- When work done in the session completes a TODO task, say so and move it to Done in the same turn (or ask, when
  it is unclear whether the task is fully done).
- A task the user abandons goes to Done with `- dropped YYYY-MM-DD` instead of `done`, and a reason, only when the
  user says to keep it; otherwise delete it from TODO.

## Housekeeping

- Keep the file ASCII apart from what titles need, and wrap lines at 120 characters.
- Commit the file with the rest of the work, following the project's git routine. Stage it by its path.
- When the user asks what is left to do, read the file and answer from TODO, in order, with the relevant notes.
