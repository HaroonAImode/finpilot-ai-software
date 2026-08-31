# Selective Sync & Conversation-Grouped Documents — Plan

**Status:** planned, not built. Review before implementation.
**Affects:** `backend/services/slack-connector`, Documents page, Connected Apps tab.
**Related:** architecture report §5.11 (connector contract).

---

## 1. The problem this actually solves

Today sync is all-or-nothing: it walks every conversation the bot can reach and
downloads every file it finds. On the test workspace that is 23 files, so it
looks fine. On a real company Slack it is not.

A three-year-old business Slack typically holds **thousands** of files, and the
large majority are not financial documents — screenshots, design mockups, memes,
exported charts, profile pictures. Syncing all of them means:

| Consequence | Why it matters |
|---|---|
| Documents page full of noise | The 40 invoices are buried under 2,000 screenshots |
| Long syncs | Every file is downloaded, hashed and stored |
| Storage cost | Paying to keep files nobody will ever open |
| **Privacy exposure** | DMs are pulled in wholesale — private conversations copied into a finance system |
| Slack rate limits | More calls than necessary, every run |

The last one is not a performance note. **A finance tool that silently copies
every private DM attachment into its own storage is a problem**, regardless of
how well the storage is secured. It should not be the default, and today it is.

So this work is not a convenience feature. It is what makes the connector
deployable somewhere real.

---

## 2. What was asked for

1. Choose specific channels/DMs to pull files from, rather than everything.
2. Show synced files grouped by where they came from — channel, then that
   channel's files; DM, then that DM's files.
3. Trigger a sync for one specific channel or DM.

All three are sound and are in the plan below as the core.

---

## 3. What I recommend adding, and why

These are not extra features for their own sake. Each one addresses a problem
already visible in the current build.

### 3.1 Discovery before selection *(makes selection usable)*

Asking "which channels should we sync?" assumes the user knows. They usually do
not — nobody remembers which of forty channels vendors post invoices in.

So: **list conversations with a signal attached** before asking anything. Slack's
`files.list` accepts a `channel` filter, so a per-channel file count costs one
cheap metadata call and no downloads. The user then sees:

```
#accounts          142 files    last active 2 days ago
#vendors            38 files    last active 1 week ago
#general           1,204 files  last active today
#design-team        892 files   last active today
```

and can make an informed choice in seconds. Without this, selection is guesswork
and people will just tick everything — which returns us to the original problem.

### 3.2 Sync scope is saved, not chosen each time *(otherwise it is tedious)*

If the channel picker appears on every sync, people will stop using it. The
selection should be **persisted configuration**: choose once, and every
subsequent "Sync now" honours it. Editable at any time from Connected Apps.

This is the difference between a setting and a prompt.

### 3.3 DMs excluded by default *(privacy)*

Public and private channels are shared workspaces. DMs are personal. The default
scope should be **channels the bot has been explicitly invited to**, with DMs and
group DMs available but off unless deliberately enabled, and a plain-language
warning when they are.

Cheap to implement, and the right default for a financial product.

### 3.4 A date window *(possibly the highest value item here)*

Most businesses only care about the current financial year. Syncing five years
of history to find this year's invoices is the single biggest source of waste.

A simple choice — *last 3 / 6 / 12 months / everything* — cuts most workspaces
down by an order of magnitude, and is easier to implement than channel
selection because Slack's history endpoints already take an `oldest` timestamp.

**Recommendation: build this first.** It delivers more benefit per unit of work
than anything else on this page.

### 3.5 Per-conversation status *(fixes a confusion already observed)*

The last real sync reported **8 of 10 conversations processed**, with two
failures that were invisible in the UI:

```
#private-test  → not_in_channel     (the bot was never invited)
D0BP1P8T0GP    → channel_not_found  (a DM it cannot read)
```

Both are fixable by the user in ten seconds — *if they know*. Right now the
number 8/10 appears with no explanation.

Grouping the Documents page by conversation gives the natural home for this:
each channel row shows its file count, when it last synced, and any error in
plain words — *"FinPilot isn't in this channel. Invite @FinPilot to sync it."*

This turns a silent failure into an obvious, actionable one.

### 3.6 Incremental sync *(stop re-downloading what we already have)*

Every sync currently re-downloads every file. Observed directly: the third run
re-fetched all 23 files that were already stored, byte for byte.

`SyncCursor` already exists in the schema and is unused for this. Each
conversation should record the timestamp it was last synced to, and subsequent
runs should ask Slack only for what is newer. A full re-scan stays available as
an explicit "Resync from scratch".

---

## 4. Design

### 4.1 Data model

Most of what is needed already exists. `Conversation` stores type, name,
`last_synced` and `sync_complete`; `File.conversation_id` already links each file
to where it came from. Three additions:

```
Conversation
  + include_in_sync   bool     default false   -- is this in scope?
  + file_count        int      nullable        -- from discovery, for the picker
  + last_error        str      nullable        -- "not_in_channel", shown in the UI
  + last_seen_ts      str      nullable        -- newest message processed (incremental)

Installation
  + sync_dm_enabled   bool     default false   -- DMs off unless asked for
  + sync_window_days  int      nullable        -- null = all history
```

No new tables. `SyncCursor` gets used for what it was designed for.

### 4.2 API

New:

```
GET  /api/v1/slack/conversations
     → [{ id, slack_id, name, type, file_count, last_synced, last_error,
           include_in_sync, is_member }]
     Cheap metadata only. Never downloads.

PUT  /api/v1/slack/conversations/scope
     { conversation_ids: [...], sync_dms: bool, window_days: int|null }
     → saves the scope; returns an estimate of what will be imported

POST /api/v1/slack/sync/  (extended)
     { conversation_ids?: [...] }   -- optional override for a one-off run
     → omitted = use the saved scope
```

Changed:

```
GET  /api/v1/slack/files/
     + &conversation_id=<uuid>   filter to one conversation
     + &group_by=conversation    returns files grouped, for the new UI
```

All of these stay behind the existing company scoping — a conversation belongs
to an installation, and an installation belongs to one company.

### 4.3 UI

**Connected Apps → "Choose what to sync"**
A list of conversations with file counts and last activity, checkboxes, a date
window selector, and a DM toggle that is off by default and explains itself. A
running estimate at the bottom: *"About 180 files from 3 channels."*

**Documents page → grouped view**
The Slack column gains a grouping toggle: *Flat* (today's behaviour) or
*By conversation*. Grouped shows collapsible sections:

```
▾ #accounts            38 files    synced 2 min ago
     invoice_8821.pdf              Invoices
     receipt_march.pdf             Receipts
▾ #vendors             12 files    synced 2 min ago
     …
▸ #private-test         —          ⚠ FinPilot isn't in this channel
                                     [ Invite instructions ]
▸ Ayesha Khan (DM)      4 files    synced 2 min ago
```

Each group header carries a **Sync this channel** button — the per-conversation
trigger that was asked for.

---

## 5. Suggested order

Ordered by value delivered per unit of work, not by the order it was described.

| Phase | What | Why this position |
|---|---|---|
| **1 ✅ built** | Date window + incremental sync | Biggest waste reduction, least code, no UI decisions |
| **2 ✅ built** | `GET /conversations` + per-conversation status & errors | Makes the 8/10 mystery visible; foundation for everything else |
| **3 ✅ built** | Grouped Documents view | The requested view; needs only phase 2's data |
| **4 ✅ built** | Saved sync scope + picker UI | The requested selection; most UI work |
| **5 ✅ built** | Per-conversation "Sync this channel" | Small once phases 2–4 exist |

Phases 1 and 2 are independently useful and could ship on their own.

---

## 5a. Phase 1 as built

`installation.sync_window_days` (NULL = all history) and
`conversation.last_seen_ts` (newest message processed). Each walk is bounded by
whichever of the two is *later*, so a 12-month window with a sync from yesterday
asks Slack for one day. Filtering happens at Slack via the `oldest` parameter,
not by fetching everything and discarding it locally.

```
GET  /api/v1/slack/sync/settings          → { sync_window_days }
PUT  /api/v1/slack/sync/settings          { sync_window_days: int|null }
POST /api/v1/slack/sync/?full_resync=true  ignore cursors, keep the window
```

Four behaviours worth knowing:

- **Changing the window clears every cursor.** Widening 3 months to 12 would
  otherwise fetch nothing new, because each conversation would resume from its
  last processed message and never ask for the older history just requested.
- **The cursor only advances after a conversation completes.** Moving it on a
  partial walk would permanently skip whatever the failure interrupted.
- **The cursor only moves forward.** Slack returns history newest-first, so an
  older message arriving later must not drag it backwards.
- **`full_resync` ignores cursors but keeps the window.** "Fetch it all again"
  should not quietly also mean "and go further back than you asked for".

### A bug this phase's live run caught

Confirmed only by running two real incremental syncs back to back once Docker
came back up: `conversations.history` was called with `inclusive=True`
unconditionally — inert before Phase 1 (no `oldest` was ever passed), and wrong
the moment Phase 1 started passing `oldest=<last_seen_ts>`, since it re-fetched
that exact boundary message on every subsequent sync. Every channel with files
re-discovered the same files, forever, on every run.

Fixed to `inclusive=False`. Verified live: 23 discovered on the first run, 20
re-discovered on the second (bug present), 0 on the third with only the fix
applied — same data, same connector.

### Known limitation

A **new reply on an old thread is not seen**. Thread replies are walked from
their parent message, so if the parent predates the cutoff it is filtered out
before its replies are reached — and a file posted today in a year-old thread is
missed.

Not worth solving with the obvious fixes: scanning all parents defeats the
purpose, and an overlap buffer only moves the boundary. Slack's own guidance for
this is event subscriptions, which section 6 rules out for now. Rare for
invoices, which are normally posted as new messages. **The remedy is a full
resync**, and it is documented rather than half-solved.

---

## 5b. Phase 2 as built

```
GET /api/v1/slack/conversations/
  → { conversations: [{ id, slack_conversation_id, name, conversation_type,
        file_count, last_synced, last_error, last_error_hint }],
      total, needs_attention }
```

Database only — no Slack calls — so it is cheap enough to poll during a sync.
File counts come from a single grouped query, not one per conversation.

`conversation.last_error` stores Slack's raw code; `last_error_hint` turns it
into advice ("FinPilot is not in this channel. Invite it to sync files from
here."). Unmapped codes still render something honest rather than a blank.
A successful run clears the error, so nobody is told to fix what is already
fixed. `needs_attention` is the count the UI can badge.

### A production bug this phase uncovered

Writing the tests exposed a crash that had been hiding. Three `logger.info`
calls passed `filename` in `extra`, which Python's logging module reserves for
the source file and rejects with `KeyError` when the record is built.

It only raises once logging is at INFO — exactly how the containers run — so
**retrying a failed download would have 500'd in deployment while every unit
test passed**. The tests only began catching it because this phase's tests
import `app.main`, which calls `configure_logging()`.

Fixed, plus a static check over every `extra={...}` in the package so the class
of bug is caught rather than the three instances.

---

## 5c. Phase 4 as built

```
POST /api/v1/slack/conversations/refresh
  → live conversations.list call, metadata only — no message walk, no downloads.
    Upserts via the same get_or_create_conversation used by a full sync (DM
    names resolve, "unknown" rows repair), then returns the same shape as
    GET /conversations/.

POST /api/v1/slack/sync/
  body: { conversation_ids?: [uuid], window_days?: int|null }
  conversation_ids omitted/empty = every conversation. window_days uses
  Pydantic's model_fields_set so an explicit "All time" (null) is told apart
  from the field being left out entirely (= don't touch the saved setting).
```

**Why refresh exists separately from the plain GET**: `GET /conversations/`
only ever shows what a previous sync already stored — empty for a brand-new
connection, since nothing has been walked yet. The picker calls `refresh`
first so there is always a complete, correctly-named list to choose from,
even before the first real sync has ever run.

**Where the tenant boundary actually sits**: `conversation_ids` are local
UUIDs that cross a process boundary — API request → Celery → worker DB
session — with no shared transaction connecting them. Trusting them at the
API layer would mean trusting a UUID nobody re-checked against who is
actually running the job. So resolution happens inside
`SyncOrchestrator._filter_to_requested_conversations`, scoped to
`installation_id` in the same query that resolves the ids — a UUID
belonging to another company's conversation is silently dropped, never
honoured. Verified live: a request naming a bogus/foreign conversation UUID
completed with `total_conversations: 0`, and a request naming one real
channel produced `total_conversations: 1` with the worker log showing only
that channel being walked.

**Frontend**: `SyncPickerDialog` — on open, calls `refresh` (always live,
`staleTime: 0`, never shows a stale list); channels and DMs render as two
checkbox groups with per-conversation file counts and error badges; a date
window is a `RadioGroup` (All time / 3 / 6 / 12 months); footer triggers
`startSync({ conversationIds, windowDays })`. Rendered from two places per
the request that the picker be available "everywhere these files
appear" — the Connected Apps card's "Sync now" button, and a new "Sync"
button in the Documents page's Slack column header — both pointing at the
same component so the scope logic exists once.

### Live verification

Ran against the real workspace after rebuilding just the `slack-connector`
and `slack-connector-worker` images (no schema change this phase, so no new
migration):

- `refresh` returned all 10 known conversations with correct names,
  including the DM resolved to "Muhammad haroon" — confirming it reuses the
  Phase 3 DM-naming fix rather than re-implementing discovery separately.
- A sync scoped to `conversation_ids: [invoices]` reported
  `total_conversations: 1`, and the worker log showed exactly one
  `Processing conversation: invoices` line — no other channel touched.
- The same request with a bogus conversation UUID (not belonging to this
  installation) completed cleanly with `total_conversations: 0` — proving
  the tenant filter drops unrecognized ids rather than erroring or falling
  back to "sync everything."
- Files landed in the database with no duplicates (`invoices` conversation
  stayed at 10 files after a second scoped run of it — the existing dedup
  logic held under a scoped resync too).

118 backend tests pass (96 carried over + 22 new for this phase, including a
dedicated tenant-isolation suite mirroring the pattern from Phase 2's
`test_files_by_conversation.py`).

---

## 5d. Phase 5 as built

A "Sync" icon-button on every conversation header in the grouped Documents
view — no new API. It calls the same `POST /sync/` from Phase 4 with
`conversation_ids: [this-one]`, `window_days` omitted so a one-click sync
respects whatever date window is already saved rather than resetting it to
All Time the way an explicit picker choice does.

The one implementation wrinkle: the existing header was one giant `<button>`
(the Radix `CollapsibleTrigger`) wrapping the whole row. A second interactive
control can't nest inside it — invalid HTML, and Radix's `asChild` would
merge both elements' click handlers into one anyway. Restructured so the
trigger button wraps only the chevron/icon/name area, and the sync button is
a sibling in the same flex row, stopping the row's click-to-expand from
firing when the sync button is clicked.

### Live verification

Clicked "Sync social" in the running app. Network tab showed
`POST /sync/ → 200`; the worker queue (solo pool, one job at a time) was
mid-way through an unrelated job at the time, so this one sat queued for
about ten seconds before running — once it did, the log showed exactly
`Processing conversation: social` and nothing else, completing at
`Discovered: 3, Downloaded: 3, Failed: 0` — the channel's full, correct file
count, no duplicates. 118/118 backend tests still pass (no backend changes
this phase).

---

## 6. Deliberately not doing

Worth stating so these do not creep in later:

- **No ML relevance scoring.** "Which channels probably have invoices" is a
  tempting model to build and a poor use of effort. File counts plus the user's
  own knowledge of their workspace is enough.
- **No auto-selection of channels.** Guessing wrong either misses invoices or
  imports noise, and the user cannot tell which. Show the data, let them choose.
- **No per-user permission model yet.** Scope is per company, not per user, until
  the Auth service grows roles beyond `admin`.
- **No real-time Slack events.** Polling on demand is sufficient; event
  subscriptions add a public webhook endpoint and a whole failure mode for
  little gain at this stage.

---

## 7. Open questions

1. **Default scope for a brand-new connection.** Options: nothing until chosen
   (safest, but the first sync does nothing and looks broken), or channels the
   bot was explicitly invited to (sensible, since inviting the bot *is* a choice).
   Leaning towards the second.
2. **Behaviour when a channel leaves scope.** Keep already-imported files, or
   remove them? Keeping is safer and less surprising; removal should be a
   separate, explicit action.
3. **Whether the date window is a hard filter or just a first-sync default.**
   A hard filter is simpler to reason about.

---

*Related: architecture report §5.11 (connector contract),
`backend/services/slack-connector/README.md`, `CHANGELOG.md`.*
