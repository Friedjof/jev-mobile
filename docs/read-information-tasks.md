# Evidence-backed information tasks

Read tasks use `intent=read_information`, `policy.mode=read_only`, one or more
`InformationRequest` records and matching `information_observed` completion
requirements. Missing app or query information becomes a structured durable
question instead of a guess.

The evidence extractor associates visible accessibility labels and values using
element values/state descriptions, parent-child and sibling relationships, and
nearby semantic structure. A returned answer is always copied from an observed
candidate. A decision provider may select between observed candidates but
cannot author the value.

Before verification, candidate construction blocks ordinary text writes,
toggles and known destructive actions. It permits target-app entry, Back,
scrolling and navigation controls relevant to the declared semantic hints.
Password fields and requests for passwords, tokens, API keys or secrets are
prohibited. Personal account information requires a separate task-scoped
approval.

The public result includes compact `answers` and `observations` with semantic
source evidence. Raw accessibility trees, coordinates and executable snapshot
refs never leave the worker boundary.
