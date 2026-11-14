# Build and check records

These files are the working log of how this project was built with an AI coding assistant, and how each piece was verified by a separate, independent check before the work moved on.

| File | What it holds |
| --- | --- |
| `build.md` | What the builder says it built for each slice of a plan, with its test evidence. A builder may only say "built", never "checked". |
| `checks.md` | What an independent checker found when it verified those claims in a fresh session with no shared context. It includes the findings that sent work back. |
| `status.md` | Which piece of work owns which files. |

Notes for readers:

- The plans these records refer to are in [`docs/plans`](../plans).
- Commit ids refer to the original working branch. It was condensed into a few commits before publishing, and the code is identical.
- `<scratch>` and `<outputs>` stand for folders on the author's computer that hold saved test output and reports. They are not part of this repository.
