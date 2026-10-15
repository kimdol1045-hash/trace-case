# Phase F evidence directory

This directory contains non-overwriting local Phase F evidence. PF-SF1-T06 load evidence records
only synthetic fixture counts, bounded timings/errors, target attestation facts, resource counters,
and gate results; it must not contain a database URL or credential. PF-SF1-T07 recovery evidence records
only target attestation, deterministic non-sensitive counts/digests, compatible-client mode, pinned
file identity/stage checks, archive inventory/hash, measured recovery metadata/timing, complete
schema/data comparisons, and response status/shape hashes. It must not contain a database URL,
credential, dump content, or response body. PF-SF1-T08 wave-7 local MinIO evidence records only
generated disposable resource identities, non-sensitive deterministic fixture metadata, versioned
canonical digests, private-access status, bounded local timings, exact cleanup counts, and preserved
inventory hashes. It must not contain credentials, database URLs, presigned URLs, tokens, or object
bytes, and it must never be described as Cloudflare R2 evidence. PF-SF1-T11 collection output and
owner-performed evidence referenced by that output contain response status/shape receipts and SHA-256
hashes, not response bodies, cookies, bearer tokens, presigned URLs, Slack signatures, reviewer tokens,
or provider payloads.

Owner evidence must use non-sensitive screenshots, JSON exports, audit extracts, videos, or
plain-text owner notes. Redact personal data and secrets before saving a file here, never use a
symlink, and record the file's lowercase SHA-256 digest in the collection file. A manual check
left without an owner, UTC `performedAt`, and every manifest-required artifact kind remains
`NOT_RUN`; do not change PF-SF1-T11 to complete on the strength of automated receipts alone.

Wave 6 T07's original four immutable local artifacts are historical, pre-remediation evidence:

| Artifact | SHA-256 | Meaning |
|---|---|---|
| [`pf-sf1-t07-wave6-20260904-b6c91d4a-seed.json`](./pf-sf1-t07-wave6-20260904-b6c91d4a-seed.json) | `be3fc0afce6774fc234652a5befa0bc1df8d0af4e19bb873e12dcfcc73049ab0` | historical seed; older fingerprint contract |
| [`pf-sf1-t07-wave6-20260904-b6c91d4a-backup.json`](./pf-sf1-t07-wave6-20260904-b6c91d4a-backup.json) | `c0c634ac26d15ab781f415c9704ecd4d210b66fa59753f41a257ca7e1c571cd7` | historical first backup; older comparator |
| [`pf-sf1-t07-wave6-20260904-b6c91d4a-backup-r2.json`](./pf-sf1-t07-wave6-20260904-b6c91d4a-backup-r2.json) | `d557297dd7dc6f69f396e62268c72be1d448d9b17a24b1ade7a219efe791b9d9` | historical ambiguous retry-2 name; **not Cloudflare R2 evidence** |
| [`pf-sf1-t07-wave6-20260904-b6c91d4a-restore-r2.json`](./pf-sf1-t07-wave6-20260904-b6c91d4a-restore-r2.json) | `7255f643c5b9bf60807c95b8fa4edfc7fa17c8583a24aab0957c3f2b7fdbe1ac` | historical ambiguous retry-2 name; **not Cloudflare R2 evidence** |

Do not overwrite or delete those four files. None supports the remediated catalog-integrity claim,
and the two `-r2` suffixes never support the still-`NOT_RUN` matched Cloudflare R2 gate.

First-remediation artifacts used explicit retry words so they could not be confused with the R2
provider. They are historical and superseded after the second independent RED:

| Artifact | SHA-256 | Meaning |
|---|---|---|
| [`pf-sf1-t07-wave6-20260904-c640954b-retry3-seed.json`](./pf-sf1-t07-wave6-20260904-c640954b-retry3-seed.json) | `b57da068e6c51f7a7342e8865fe5d1d2398dd4f566f3810f96dcf1b97abfa179` | historical first-remediation fixture seed |
| [`pf-sf1-t07-wave6-20260904-c640954b-retry3-backup.json`](./pf-sf1-t07-wave6-20260904-c640954b-retry3-backup.json) | `3f735a01279f6d0250706481210a1da8afae1ddc25d791f72bd114c4e12ce0c3` | retained intermediate comparator input; no successful restore |
| [`pf-sf1-t07-wave6-20260904-c640954b-retry4-final-backup.json`](./pf-sf1-t07-wave6-20260904-c640954b-retry4-final-backup.json) | `20081fff77b0ac8b7c57b340f328134b2362a91778ed4f4d8825342bb9e85ffd` | historical superseded first-remediation backup |
| [`pf-sf1-t07-wave6-20260904-c640954b-retry4-final-restore.json`](./pf-sf1-t07-wave6-20260904-c640954b-retry4-final-restore.json) | `849e1fb76a08721e9dd1d1b9e777f9abc32b63d8e6dd3073da43eb92355be1ad` | historical superseded abbreviated post-smoke evidence |

The retry5 second-remediation artifacts remain immutable historical evidence. They were superseded
because archive inventory staging changed after their creation, so they are not final-code
authoritative evidence:

| Artifact | SHA-256 | Meaning |
|---|---|---|
| [`pf-sf1-t07-wave6-20260904-a91d50e8-retry5-final-seed.json`](./pf-sf1-t07-wave6-20260904-a91d50e8-retry5-final-seed.json) | `bf32440ff186a2458d3310ccc63501b07a93b20f4a6b8e111c5059a49783e5d6` | historical superseded retry5 fixed fixture seed |
| [`pf-sf1-t07-wave6-20260904-a91d50e8-retry5-final-backup.json`](./pf-sf1-t07-wave6-20260904-a91d50e8-retry5-final-backup.json) | `4dbe36c55ad7d20db700097acf3d8dbe3b1f6c9af5aace135463660eac5c07d2` | historical superseded pre-final-inventory-stage backup |
| [`pf-sf1-t07-wave6-20260904-a91d50e8-retry5-final-restore.json`](./pf-sf1-t07-wave6-20260904-a91d50e8-retry5-final-restore.json) | `ee85ca0ed0969e9ff9693350c47d8b3e86f2be6ca499eef77b99219ebd65cde7` | historical superseded pre-final-inventory-stage restore |

The authoritative second-remediation artifacts were regenerated with the final archive-inventory
implementation as new single-link mode-0600 files and are never overwritten:

| Artifact | SHA-256 | Meaning |
|---|---|---|
| [`pf-sf1-t07-wave6-20260904-b72f64c9-retry6-final-seed.json`](./pf-sf1-t07-wave6-20260904-b72f64c9-retry6-final-seed.json) | `d3984780a8b3cc841f5c8124ed391e1ef2bc6feaaae070a408af63de9316eb91` | authoritative retry6 fixed fixture seed |
| [`pf-sf1-t07-wave6-20260904-b72f64c9-retry6-final-backup.json`](./pf-sf1-t07-wave6-20260904-b72f64c9-retry6-final-backup.json) | `c49a8c52fe6fb10674f8f8a6a591b229c1c6271a81669094fd942459c1705cc7` | authoritative final-code descriptor-bound SHA/archive inventory evidence |
| [`pf-sf1-t07-wave6-20260904-b72f64c9-retry6-final-restore.json`](./pf-sf1-t07-wave6-20260904-b72f64c9-retry6-final-restore.json) | `0e197c1796ad39be92cf2254bfbd92e5bb91fbe0ce2a05edca1ea09cd3b37f4b` | authoritative final-code pinned restore and complete post-smoke equality |

See the
[wave 6 verification report](../sprint-f1-wave6-verification.md) for the execution boundary, hashes,
metrics, cleanup proof, and remaining `NOT_RUN` gates.

Wave 7 T08's authoritative local MinIO recovery set and retained fail-closed history are immutable,
regular, single-link, mode-0600 files:

| Artifact | SHA-256 | Meaning |
|---|---|---|
| [`pf-sf1-t08-wave7-20260904-d31c8e7a-retry1-failure.json`](./pf-sf1-t08-wave7-20260904-d31c8e7a-retry1-failure.json) | `1c673b8f9ead029d1656a9ccf08f9e8b4a1b95996751d2586e71f427c8c17593` | retained fail-closed fixture-transition history |
| [`pf-sf1-t08-wave7-20260904-e29f6c4b-retry2-failure.json`](./pf-sf1-t08-wave7-20260904-e29f6c4b-retry2-failure.json) | `87e6d9a901aa36c5389bb13cb16986efbe63ac77766c5438cbf56ff6ae070872` | retained fail-closed attempt-transition history |
| [`pf-sf1-t08-wave7-20260904-a47d91c6-final-manifest.json`](./pf-sf1-t08-wave7-20260904-a47d91c6-final-manifest.json) | `0ae1658cb4b731008f050bc55e191f22fadf0d68d2a65d941c51e7471a1c97fc` | authoritative matched manifest and source bindings |
| [`pf-sf1-t08-wave7-20260904-a47d91c6-final-recovery.json`](./pf-sf1-t08-wave7-20260904-a47d91c6-final-recovery.json) | `8d2c0a0d9a5a18867f2aa81c3684cc1ee25dba58fe781fdd1852f9424f153976` | authoritative restored DB/API/object-store validation |
| [`pf-sf1-t08-wave7-20260904-a47d91c6-final-cleanup.json`](./pf-sf1-t08-wave7-20260904-a47d91c6-final-cleanup.json) | `34e72df84d23848062b2649bb3e19d76748f32bffcfd1a75c13345a16043a000` | exact cleanup, absence, and prior-state preservation |

The final set proves a matched 12-row/12-object recovery with all supported attachment owner types
and all three fixture MIME classes, all-page dry-run validation, full-byte coverage, and exact
cleanup. See the [wave 7 verification report](../sprint-f1-wave7-verification.md). The local MinIO
disposable sub-gate is PASS; `PF-SF1-T08`, actual Cloudflare R2 recovery/versioning/backup,
separately hosted staging, operator handoff, production RTO/RPO, and the Phase F release gate remain
unchecked/`NOT_RUN`.

Wave 8 T09's authoritative remediated local network-free full-reindex artifacts are immutable
regular, single-link, mode-0600 files:

| Artifact | SHA-256 | Meaning |
|---|---|---|
| [`pf-sf1-t09-wave8-local-disposable-sg9c42fd-20260907.json`](./pf-sf1-t09-wave8-local-disposable-sg9c42fd-20260907.json) | `13489b5388989d5ee6a34016e03191def750b44c7025f4b7afc7b37da482eb76` | authoritative official LightRAG indexing, equal semantic graph digest across three generations, CAS, resolver, failure/fallback and retention evidence |
| [`pf-sf1-t09-wave8-cleanup-sg9c42fd-20260907.json`](./pf-sf1-t09-wave8-cleanup-sg9c42fd-20260907.json) | `76d1dbdd2d77ff17b1edff866dafe70dd41f5d0b2d3f270b31030ed56f7f0f84` | authoritative exact cleanup, absence and pre-existing-state/evidence preservation evidence |

The earlier unnamed pair and the `-final-` pair remain immutable historical evidence. The unnamed
pair predates the separate production-child `SIGKILL` injection; the `-final-` graph digest included
workspace-specific AGE internal IDs and therefore did not prove semantic equality across the three
generations. All six files omit corpus/query text, credentials, database URLs, permalinks and tokens.
Only the `sg9c42fd` pair is authoritative, and it proves only the local disposable network-free
sub-gate; staging credentials/bootstrap, live providers,
staging apply, production-like networking/load/alerts, operator handoff, production RTO/RPO,
`PF-SF1-T09`, and the Phase F release gate remain `NOT_RUN`/open. See the
[wave 8 verification report](../sprint-f1-wave8-verification.md).

Wave 9 T10's authoritative local migration round-trip artifacts are immutable regular, single-link,
mode-0600 files:

| Artifact | SHA-256 | Canonical contract SHA-256 | Meaning |
|---|---|---|---|
| [`pf-sf1-t10-wave9-local-disposable-w9rt7c3b-20260907.json`](./pf-sf1-t10-wave9-local-disposable-w9rt7c3b-20260907.json) | `8ce31fb7f0e6e5b043ef7419c84ebbce88786b783dffeb6c73dead268b82a486` | `3417f99f0859c8611af7235e798647967775d0ab3ae5c2f66547c09094a8de62` | read-only snapshot/dump source binding, exact `0030 → 0029 → 0030` invariants, normalized equality and no-external-call smoke |
| [`pf-sf1-t10-wave9-cleanup-w9rt7c3b-20260907.json`](./pf-sf1-t10-wave9-cleanup-w9rt7c3b-20260907.json) | `9c5941a7bbbaadfff3731ea693d525c9562a982a6f8b93a1918368c5a377a22a` | `f3fc82bdda5dc567c1340181b14a4c4236545e096b345769721577963fd3a38f` | evidence-bound exact cleanup, resource/process absence and pre-existing-state/evidence preservation |

The pair omits database URLs, credentials, raw rows, API response bodies, Slack data, tokens and
attachment contents. It proves only the `PF-SF1-T10` local disposable sub-gate; staging, live service,
operator handoff, production RTO/RPO, the task, Phase F and release gates remain unchecked/`NOT_RUN`.
See the [wave 9 verification report](../sprint-f1-wave9-verification.md).

Wave 10 T11's retained remediated local isolated physical-browser artifacts are immutable regular,
single-link, mode-0600 files; `92af97f8` is the authoritative final-code set:

| Artifact | File SHA-256 | Canonical contract SHA-256 | Meaning |
|---|---|---|---|
| [`pf-sf1-t11-wave10-r10g4c7f-local-physical-browser-20260907.json`](./pf-sf1-t11-wave10-r10g4c7f-local-physical-browser-20260907.json) | `6bffce9b456ddad3453cbb5216fc833bd56d05e55c405ca98f55ca6da8dd8231` | `69d9130be44122291ecb3bd577e24613c730bd378f397a8d78cab8fb92af97cc` | historical actual-product proof before the production-path sealed quality-candidate seed was added |
| [`pf-sf1-t11-wave10-r10g4c7f-cleanup-20260907.json`](./pf-sf1-t11-wave10-r10g4c7f-cleanup-20260907.json) | `bac7f9413f3367d367358d9a23223341cf7c8a5bfd04622cebc2bff9b4f46c50` | `b7785fc08ca125d8da9edf6122f52870a4364646b6e7ca1f40328ca30bcd157e` | evidence-bound exact resource/process/profile/temp/port absence and pre-existing Docker/database/evidence/current-`tc` preservation |
| [`pf-sf1-t11-wave10-r10g4c7f-desktop-20260907.png`](./pf-sf1-t11-wave10-r10g4c7f-desktop-20260907.png) | `475e4c3efc4deb11c9bd638cbb7c0126914495b84e20c372e3940d27446e1291` | — | bounded synthetic desktop report-reconciliation screenshot |
| [`pf-sf1-t11-wave10-r10g4c7f-mobile-390-20260907.png`](./pf-sf1-t11-wave10-r10g4c7f-mobile-390-20260907.png) | `0b102dc9e920c08facb0ead8f29348492e835f58339792e8ae72be7b22a7d4c6` | — | bounded synthetic 390×844 execution/detail screenshot |
| [`pf-sf1-t11-wave10-r10k8a3d-local-physical-browser-20260907.json`](./pf-sf1-t11-wave10-r10k8a3d-local-physical-browser-20260907.json) | `4b4bbc028e8b191e337fef5db6dd69e67f7d6b23c4a620829b62546522781544` | `d58da65cd5228bcdb1fff6b868d83ac3514e2a3aeb1c2b3e5a14d3754c7af846` | historical pre-independent-RED source binding; observed aborts were safe-shaped, but the bound producer/validator could accept unsafe teardown aborts |
| [`pf-sf1-t11-wave10-r10k8a3d-cleanup-20260907.json`](./pf-sf1-t11-wave10-r10k8a3d-cleanup-20260907.json) | `7808bc0252f59b15fcedb9ef4000e5f25e4123d9f3924fd0ec3ab7ac3efdbad0` | `c9a4f1fc9d415794d8e89fcda05ace971fecad7822d22c316d16763ae9a38284` | evidence-bound exact resource/process/profile/temp/port absence and pre-existing Docker/database/evidence/current-`tc` preservation |
| [`pf-sf1-t11-wave10-r10k8a3d-desktop-20260907.png`](./pf-sf1-t11-wave10-r10k8a3d-desktop-20260907.png) | `264cb6c3ecec8c332abce67dc2fccc4f6a591a58589259c498446aeee4310d5c` | — | bounded synthetic desktop report-reconciliation screenshot |
| [`pf-sf1-t11-wave10-r10k8a3d-mobile-390-20260907.png`](./pf-sf1-t11-wave10-r10k8a3d-mobile-390-20260907.png) | `d110ee81d7fa708a680ef5d8ad47edb29bd214bf3437da8fdf222f27bba0fca6` | — | bounded synthetic 390×844 execution/detail screenshot |
| [`pf-sf1-t11-wave10-92af97f8-local-physical-browser-20260907.json`](./pf-sf1-t11-wave10-92af97f8-local-physical-browser-20260907.json) | `b7d8ad10a2f88619479b7983302033f048fbe40fd70b19359d99b565f90d8946` | `360d379dc8b6d7e71ef1199f10c75ad9450cdd8cb66b0713a959ea2f0a3497f5` | authoritative post-RED actual-product proof; 15 PASS from 37 assertions, only exact browser-port allowlisted GET/fetch abort receipts, 22 mobile surfaces and exact manifest/check/validator binding |
| [`pf-sf1-t11-wave10-92af97f8-cleanup-20260907.json`](./pf-sf1-t11-wave10-92af97f8-cleanup-20260907.json) | `fe648c99521e5588884d34620dd794e10b7cdcfc8adcf7419cd4ad896e4fe39b` | `4665de24977388921cf10106669f0db54fc68544971a7bc7ab0128842dc34bf2` | evidence-bound exact resource/process/profile/temp/port absence and pre-existing Docker/database/evidence/current-`tc` preservation |
| [`pf-sf1-t11-wave10-92af97f8-desktop-20260907.png`](./pf-sf1-t11-wave10-92af97f8-desktop-20260907.png) | `f605f211fc2dd1fff71a8bd08afe5b6902693a606d8d9783e6f3583ef8f2b5c5` | — | inspected bounded synthetic desktop report-reconciliation screenshot |
| [`pf-sf1-t11-wave10-92af97f8-mobile-390-20260907.png`](./pf-sf1-t11-wave10-92af97f8-mobile-390-20260907.png) | `9aaa44413a596febc16ee7d0f383ad42f9819538aec6f1c7cb5c66f2b2cfc37d` | — | inspected bounded synthetic 390×844 execution/detail screenshot |

All earlier Wave 10 PASS sets—including `w10a7c3d`, `w10b7c4e`, `w10c7d5f`, `8c9357ca`,
`r10d8f1c`, `r10g4c7f` and `r10k8a3d`—are retained without overwrite as historical source bindings.
Independent RED `task_0c95d0badb67` invalidated `r10k8a3d` authority because its producer and
validator could launder an aborted MinIO PUT after harness teardown; remediation
`task_d1aff5905dae` and `92af97f8` bind the fail-closed request-resolution code. The final JSON records all 19 mobile routes plus the
generation-failure detail, quality-review candidate detail and execution modal, all aborted requests, zero unresolved
request/HTTP/console/page/external failures, exact cleanup and the unchanged current-`tc` digest.
Wave 10 proves only the local isolated automated sub-gate (15 PASS, 0 automated `NOT_RUN`); all 15
human checks and PF-SF1-T11, Phase F, deployed HTTPS, external integration, production and release
gates remain `NOT_RUN`. See the [wave 10 verification report](../sprint-f1-wave10-verification.md).

Load, recovery, and UAT evidence files should follow the approved retention and review process. This
tracked README does not itself count as execution or UAT evidence.
