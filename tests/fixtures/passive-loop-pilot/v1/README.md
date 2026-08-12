# Passive-loop multi-repository pilot sheets

`example-sheet.json` is a deterministic schema and scoring example used by CI. It uses
`observation_status: simulated` and `pilot.status: harness-only`. It is not a multi-week live
pilot and must never be cited as one.

A live pilot records a separate sheet with the same contract after at least two materially
different repositories complete the protocol in `docs/passive-loop-pilot.md`. That sheet must set
`observation_status: executed` for every counted repository and may set
`acceptance.live_execution_complete` only when the longitudinal run and review cadence are finished.

Neither kind of document may contain a prompt, task text, transcript, tool arguments, or commands.
