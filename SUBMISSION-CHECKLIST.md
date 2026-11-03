# Uplink submission checklist

Meta Global AI Developer Hackathon (Cerebral Valley). Team **Uplink**, Indie track, solo
(Richard Suarez). Deadline: **Wednesday, November 4, 2026, 8:00 PM CT (9:00 PM ET), no
grace period.** The form stays editable until the deadline; there is one submitter.

Model: **base Meta Muse Glimmer 30B**, unmodified Q4_K_M GGUF (SHA-256 prefix
`4cc57c0f`), served on-device by `llama-server` (llama.cpp b10353) on localhost. The model uses
base, unmodified open weights.

## Rubric coverage

| Criterion | Weight | Where it is met | Evidence |
| --- | ---: | --- | --- |
| Stage 1: theme fit (Build for Your World) and real Meta model use | pass/fail | A clear user and job (get an offline computer back online), base Glimmer in the loop | [README](README.md#what-it-does), [evidence/f04-series/](evidence/f04-series/) |
| Impact | 30% | Outage-time help that a cloud assistant cannot give; named users; honest limits | [README: Why it matters](README.md#why-it-matters-impact) |
| Use of Meta AI tooling | 25% | Base Glimmer open weights on-device via `llama-server`, native chat template, grammar-constrained typed tools, hash-pinned model | [README: How it uses Meta AI](README.md#how-it-uses-meta-ai), [templates/manifest.json](templates/manifest.json) |
| Technical depth | 20% | Independent ONLINE oracle, snapshot-only repairs with exact diff, 60 s detached revert, OOB control link, preregistered series | [README: Architecture](README.md#architecture), [README: Safety model](README.md#safety-model), `evidence/f04-series/*/PREREG.json` |
| Demo | 25% | 3-minute video (cut r12q7, 171.90 s) with a real recorded recovery and the honest limits | [README: Demo video](README.md#demo-video), [DEMO-SCRIPT.md](DEMO-SCRIPT.md) |
| Bonus: Best Build on Meta Open Weights | bonus | Unmodified open Glimmer weights, our own hardware, no cloud at decision time | [README: How it uses Meta AI](README.md#how-it-uses-meta-ai) |
| Bonus: Best Agentic Use Case | bonus | Observe → typed action → bounded execution → independent verification, auto-revert, honest Report | [README: What it does](README.md#what-it-does) |
| Bonus: Most Viral | bonus | Needs a published X post and its link in the form (owner decision) | Draft below |

## Kickoff rules

- [x] Repository created on or after Oct 26; first commit `dabff4c` (Oct 26, 2026, 11:40 AM CT); incremental history.
- [x] Pre-existing work called out: Glimmer weights, llama.cpp and Python libraries ([README](README.md), [PROVENANCE.md](PROVENANCE.md)).
- [x] Source-available license: Apache-2.0 ([LICENSE](LICENSE)); upstream terms in [licenses/manifest.json](licenses/manifest.json).
- [x] Not a chat wrapper, simple RAG, image analyzer or medical advice: an on-device agent taking typed, auto-reverting actions on a real OS.
- [x] Video at or under 3:00 (171.90 s).
- [ ] Repository public at submission (owner action).
- [ ] Video uploaded and viewable by anonymous judges (owner action).
- [ ] Video opens with one line on what was pre-existing (current cut opens "Built entirely during the hackathon."; confirm or re-voice the first line).

## Form fields (13, per the saved form snapshot; confirm against the live form)

| # | Field | Content | Status |
| ---: | --- | --- | --- |
| 1 | Team Name* | Uplink | Ready |
| 2 | Team Members | Richard Suarez (solo) | Owner confirms |
| 3 | Track* | Indie Developer | Ready |
| 4 | Problem statement* | Build for Your World | Ready |
| 5 | X post link (optional) | Blank unless an approved post is live | Owner decision |
| 6 | Project Description* | Final text in the submission packet; carries the pre-existing line and both bonus claims | Ready; check the character limit |
| 7 | Public GitHub repo* | `https://github.com/rgsuarez/uplink` | Blocked until the owner makes it public |
| 8 | 3-minute video URL* | `VIDEO_URL_PLACEHOLDER` | Blocked until upload |
| 9 | Use of Meta products* | Final text in the submission packet | Ready |
| 10 | Why Meta's models* | Final text in the submission packet | Ready |
| 11 | Feedback on Meta products (optional) | Draft in the submission packet | Owner approves, edits or omits |
| 12 | Feedback on partner tech (optional) | Draft in the submission packet | Owner approves, edits or omits |
| 13 | Consent to promotional use | No default | Owner decides |

## Results table rule

Only committed evidence goes into the README results table. Every trial that reached the
model is counted, pass or fail; pre-model stops are listed and excluded. Rows for series
not committed by the Wednesday 11:00 AM CT test freeze are deleted, not estimated.

| Series | Status at this commit |
| --- | --- |
| F04 bad DNS (`54b5899ccb66`, `075540cb0307`, `51ffa1727d58`) | Committed: 10/10, median 15.319 s, 0 false ONLINE |
| F06 stale proxy | **5/5 PASS**: `restore_proxy` every trial, median 10.395 s, 0 false ONLINE, 0 aborts. Two segments: A `98e499eebbd7` 2/2 at 24.5 GiB entry max; B `15a6104a9685` 3/3 after entry max raised to 24.75 GiB. Six pre-model headroom refusals excluded from trial count. Evidence: [F06-COMBINED-TALLY.md](evidence/f06-series/F06-COMBINED-TALLY.md); [SERIES-LEDGER.json](evidence/f06-series/98e499eebbd7/SERIES-LEDGER.json); [SERIES-LEDGER.json](evidence/f06-series/15a6104a9685/SERIES-LEDGER.json) (committed source `c3dd1ad`; sanitized public derivatives). |
| Control: healthy network | **0/3 FAIL**: `declare_online` lacked the required `verified` reason; safety held: 0 network changes, 0 false-action requests, 0 false ONLINE. Evidence: [SERIES-LEDGER.json](evidence/controls-series/0ea3e4e5eebc/SERIES-LEDGER.json) (committed source `d8b390a`; sanitized public derivatives). |
| Control: misleading status text | **0/3 FAIL**: `declare_online` lacked the required `verified` reason; safety held: 0 network changes, 0 false-action requests, 0 false ONLINE. Evidence: [SERIES-LEDGER.json](evidence/controls-series/0ea3e4e5eebc/SERIES-LEDGER.json) (committed source `d8b390a`; sanitized public derivatives). |
| F05 wrong gateway, site-only outage, F01 Wi-Fi off | Pending (run only if approved and finished before the freeze) |

In these 6 controls, the base Glimmer model never changed the network under either healthy or
misleading-status conditions (0 network mutations, 0 false-action requests, 0 false ONLINE),
but all 6 health reports failed the oracle's required `verified` reason check (0/6 PASS);
5 pre-model headroom refusals are retained and excluded from the trial denominator.

## Before Submit

- [ ] Test freeze Wed 11:00 AM CT; commit the final ledgers.
- [ ] Update the README results table from committed ledgers; remove every PENDING row and placeholder.
- [ ] Recompute the numbers with the README reproduction script; they must match the table.
- [ ] Confirm the video voiceover numbers match the table (current VO: 10 model-reached trials, DNS restored every time, median about 15.3 s, zero false online).
- [ ] Owner approves: repository public plus push; video upload; optional X post.
- [ ] Fill the form, verify anonymous access to the repo and video links, then the owner presses Submit and keeps the receipt.

## Optional X post draft (unposted)

```text
Uplink: when Wi-Fi breaks, cloud AI breaks too. So Meta's open Muse Glimmer runs on the laptop, picks one typed fix, and independent checks decide. 10/10 live DNS trials recovered, median 15.3 s, 0 false online. VIDEO_URL_PLACEHOLDER https://github.com/rgsuarez/uplink
```

About 260 characters with X's 23-character link counting; add any hashtag or handle the event requires.

Nothing in this checklist publishes, posts or submits anything.
