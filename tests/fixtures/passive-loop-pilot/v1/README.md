# Passive-loop multi-repository pilot sheets

`example-sheet.json` is a deterministic schema and scoring example used by CI. It uses
`observation_status: simulated` and `pilot.status: harness-only`. It is not a multi-week live
pilot and must never be cited as one.

A live pilot records a separate sheet with the same contract while the protocol in
`docs/passive-loop-pilot.md` runs. `live-cohort-2026-08-12.json` is the in-progress first-review
sheet: counted repositories are `executed`, but `acceptance.live_execution_complete` stays false
until the multi-week window and retained-integration exit finish.

Neither kind of document may contain a prompt, task text, transcript, tool arguments, or commands.
