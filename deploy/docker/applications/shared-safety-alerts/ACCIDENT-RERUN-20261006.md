<!--
SPDX-FileCopyrightText: 2026 the VSS application contributors
SPDX-License-Identifier: Apache-2.0
-->

# Accident and aftermath rule: replay on 6 October 2026

Applied the user-approved short prompt to `traffic_accident` on the existing
`accident_detection_live` camera, using the same shared VLM, VIOS, Alert Bridge,
Kafka, Elasticsearch, and UI.

## Latest replay: 8-second windows, 25% overlap

On the next requested replay, deleted the nine preceding accident alerts and
replaced the accident rule with 8-second windows and 2-second overlap, giving
a 6-second step. Preserved the 87 unrelated incident documents and all three
other saved rules as they existed when cleanup began. The same user-approved
prompt below was retained.

The original 19.44-second video played once at normal speed after a 12-second
blank setup lead-in. Monitoring was ready after 4.384 seconds. Native inference
completed successfully with five valid classifications: No, Yes, Yes, No, No.
Full windows contained 32 frames at 4 FPS. Two positive windows were indexed;
the nine deleted incident IDs did not reappear. Publisher and independent reader
both exited successfully, and frame hashes verified moving footage through EOF.

Both fresh alert clips and snapshots returned HTTP 200, and both clips decoded
without errors. The first thumbnail is black because its alert window starts
during the synthetic lead-in; its clip includes moving traffic and the rider
fall. The second thumbnail shows the road scene. These are two overlapping alert
windows, without event suppression or deduplication. The model's collision-partner
descriptions remain inconsistent, so this result does not validate every cue.

Current saved rule: `a31dc728-8ad2-4c19-b9c9-05932a6cb5d5`. The finite feed
has ended. Evidence and media checks are in
`_builds/accident-eight-second-once-20261006T064003Z/validation.json`, with full
cleanup backups in the matching `-cleanup/` directory. No service restart was
needed.

## Previous replay: 4-second windows, 50% overlap

```text
Answer Yes when the video clearly shows a traffic accident or its aftermath, including a vehicle collision, rollover, rider crash, or visibly crashed vehicles and fallen riders.

Answer No for normal traffic, near misses, or unclear evidence. Describe only what is visible.

Return JSON with two brief "visual_cues" and "decision" set to "Yes" or "No".
```

Deleted seven earlier accident incident documents, scoped to this category and
camera identity. Saved a full backup and deletion receipts in
`_builds/accident-aftermath-once-20261006T0557Z-cleanup/`. Verified zero matching
old alerts before the replay, preserved 90 unrelated incidents and three unrelated
saved rules, and replaced the preceding accident rule. The public Elasticsearch
route rejects administrative deletes; cleanup used Elasticsearch's local
administrative endpoint inside its container.

The 19.44-second input played once at normal speed, after a 12-second blank
lead-in for admission. No frame from the accident footage was frozen for setup.
Monitoring was ready after 3.996 seconds, before the input
footage began. The original file was untouched, and the existing camera UUID
was reused. No shared service was restarted or duplicated.

| Result | Value |
| --- | --- |
| Complete classified windows | 15 |
| Valid responses with two visual cues | 15 |
| Positive, indexed alert windows | 9 |
| Blank-only startup windows | 3, all No |
| Publisher and independent reader EOF | Verified |
| Native inference completion | Successful |

The accident and visible aftermath generated positive responses. Overlapping
windows are separate stored alert rows, not distinct crash events. Model
observations still contain inconsistent vehicle identities and unsupported
claims of damage; this run validates the saved rule, stream playback, and alert
transport, not every descriptive claim or general accident-detection accuracy.

Saved rule: `2b4b35b5-1392-412f-ab93-2a4a7b8ac2da`. The API prompt was checked against the application
configuration. All nine stored alert rows carry this exact prompt, and none of
the deleted incident IDs reappeared. The rule remains saved in the UI; the finite
camera feed is offline after EOF.

Replay evidence: `_builds/accident-aftermath-once-20261006T0557Z/` includes all
Kafka classifications, native VLM logs, indexed incidents, frame hashes, input
and API snapshots, publisher logs, EOF evidence, and `validation.json`.
