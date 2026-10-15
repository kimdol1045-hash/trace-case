# Review-candidate anonymization and fingerprint contract

Status: privacy foundation, integrated into the T08 candidate/export/review path. This contract
still does not approve, attest, certify, publish, or retain any real sample. The normative wire schema is
`packages/contracts/review-candidate/1.0.0/schema.json`; the normative Python implementation is
`backend/src/tc_backend/privacy/review_candidates.py`.

## Boundary

`REVIEW_CANDIDATE` is a privacy envelope, not a quality report or release certificate. It has the
fixed contract version `review-candidate-envelope/1.0.0` and exactly one declared data class:

- `SYNTHETIC` means every source value is synthetic. The caller must declare
  `source_is_synthetic=True`; relabelling actual data as synthetic fails closed.
- `ACTUAL_MINIMIZED` means the source was actual, but the public payload contains only the
  scanner-clean minimized projection. It does not mean anonymous, de-identified for every legal
  regime, safe to publish, or approved for model training.

Raw source values exist only in caller memory while `seal_source_field` computes an HMAC. They are
not returned. The envelope exposes only the key ID, privacy domain, field domain, and keyed seal.
Keys are supplied as at least 32 bytes for each call. The module never reads process environment,
configuration, files, or a key service and never logs keys or inputs.

The public payload is recursively scanned. Sensitive key names are replaced with synthetic
`redactedFieldN` names and a sensitive string or numeric leaf is replaced as a whole. `REJECT`
mode rejects the candidate instead of redacting it. Redaction findings expose categories and
structural ordinals only; they never contain excerpts, original field names, raw paths, values, or
key material.

The boundary rejects or redacts:

- private-key blocks, Bearer values, common provider/Slack/GitHub/AWS/JWT token forms, and generic
  credential assignments;
- email, Korean and international phone forms, Korean resident/foreigner/business identifiers,
  IPv4, IPv6, MAC addresses, URLs, and absolute POSIX/Windows/UNC paths;
- Slack direct IDs/timestamps, Jira keys, direct identifier fields, and customer, order, account,
  person, payment, card, PAN, and IBAN patterns;
- raw source/content/body/message/prompt/excerpt fields and raw SHA-256 content values or fields.

Scanning runs after NFKC normalization, removal of Unicode default-ignorable format characters,
and a pinned confusable skeleton for common Cyrillic and Greek substitutions in values. After the
same normalization, public projection property names must match the ASCII letter-led
`[A-Za-z][A-Za-z0-9_-]{0,127}` grammar. Both producer and schema apply the same conservative,
case-explicit substring patterns to that normalized name; singular, plural, camelCase, snake_case,
kebab-case, and embedded sensitive markers therefore fail closed instead of relying on token or
prefix boundaries. A non-ASCII or otherwise ambiguous public property name is also sensitive and
is rejected or replaced. The redactor scans its own output again and raises
`REDACTION_RESIDUAL_SENSITIVE_DATA` unless it can prove that the result is clean. Pattern scanning
is intentionally conservative and can produce false positives. It is one safety layer, not a proof
that all possible personal or confidential information has been removed.

## Normalization and canonical bytes

`review-candidate-nfkc-lf-ignorables-v1` is pinned as follows:

1. Input must be JSON-compatible, no deeper than 64 levels, with at most 1,000 properties per
   object and 10,000 entries per array.
2. Strings and property names use Unicode NFKC. CRLF and CR become LF. Unicode `Cf` format
   characters are removed.
3. Two property names that collide after normalization reject the whole value.
4. Integers are limited to the interoperable range `[-(2^53-1), 2^53-1]`; floats must be finite;
   negative zero becomes zero.
5. Object properties are serialized by normalized key order with UTF-8, no insignificant
   whitespace, and unescaped Unicode. Array order is significant and preserved.

Consequently, mapping insertion order and source-field mapping order do not change a candidate.
Array order changes identity unless the caller first uses `deterministic_sample`. That sampler
ranks scanner-clean canonical items by a domain-separated digest, so population input order does
not change the selected sample.

## Source seals

The source-seal message is a concatenation of five length-framed byte strings. Each frame is an
unsigned eight-byte big-endian length followed by its bytes:

1. `tc.review-candidate.source-seal.v1`
2. `keyId`
3. `privacyDomain`
4. `fieldDomain`
5. pinned canonical JSON bytes of the raw field value

The output is `hmac-sha256:<lowercase hex>`. Key ID, privacy domain, and field domain are all
explicit inputs. Changing the key, key ID, privacy domain, or field domain changes the seal. That
separation prevents accidental linking across privacy domains and prevents a seal for one field
from being reused as another. It does not protect a weak or compromised key and does not replace
external key rotation, access control, or audit.

`verify_source_seals` is available only to an authorized caller that already holds both the raw
values and key bytes. A missing field, revision drift, changed value, wrong key, or different domain
raises a stable source-free error.

## Public fingerprints and revisions

Public SHA-256 is used only after sanitization or over envelope metadata whose source values are
already HMAC seals. Its framed preimage is:

1. `tc.review-candidate.public-fingerprint.v1`
2. explicit fingerprint field domain
3. pinned canonical JSON bytes

The three recomputable identifiers are:

- `candidateKey`: domain `candidate-key`, over purpose, data class, and the strict source-seal
  object. It is stable for revisions of the same sealed candidate and changes if source identity,
  key, or privacy domain changes.
- `payloadFingerprint`: domain `payload`, over only the scanner-clean payload projection.
- `artifactFingerprint`: domain `artifact`, over the complete strict envelope except
  `fingerprints.artifactFingerprint` itself. It includes the candidate key, payload fingerprint,
  review/attestation/retention/certification states, and every other declared field.

`verify_review_candidate` recomputes all three and accepts an `expected_revision`. A stale expected
revision raises `REVISION_CONFLICT`; a changed source seal, payload, state, timestamp, retention
value, or declared fingerprint fails recomputation. Revision one must have no predecessor and must
remain `UNREVIEWED`, attestation `NOT_RUN`, certification `NOT_CERTIFIED`, and `eligible: false`.

The source HMAC and public hashes serve different purposes. A public SHA is not a safe replacement
for a source HMAC: low-entropy identifiers can be guessed offline. Raw content SHA fields are
therefore forbidden from the payload even when they look opaque.

## Retention defaults

The revision-one builder sets `disposition: DELETE` and calculates `expiresAt` from the explicit
UTC `createdAt` value:

- synthetic candidates: at most 90 days, default 90 days;
- actual-minimized candidates: at most 30 days, default 30 days.

Callers may select a shorter positive duration but not a longer one. The verifier recomputes the
expiry exactly. This foundation records retention intent only: no deletion scheduler, object-store
lifecycle, database TTL, legal hold, or deletion receipt is implemented here. An integration must
enforce deletion independently and define what happens to backups and derived artifacts.

## Review, attestation, and certification limitations

The local scanner result is recorded as sanitization `CLEAN`; it is not the independent privacy
attestation. Fresh exports always set attestation to `NOT_RUN`. Likewise, creating deterministic
fingerprints proves only byte-level integrity under this contract. It does not prove source truth,
representativeness, label correctness, reviewer authority, consent, legal basis, privacy safety,
quality-gate success, or fitness for release.

The schema reserves typed fields for a later reviewed/attested revision. The Python foundation
intentionally refuses `CERTIFIED` or `eligible: true`, because it has no review-authority verifier
or certification policy. A real candidate remains ineligible until a separate, approved workflow
binds reviewer authority, independent attestation, sampling evidence, and a scoped certification
artifact to a later immutable revision.

No real sample was supplied to, inspected by, or processed during this implementation. The tests
use synthetic strings only. Passing them demonstrates deterministic behavior and rejection of the
covered patterns; it cannot certify any operational dataset or real export.

## T08 integration and remaining work

The T08 exporter, import service, PostgreSQL persistence, list/detail API, web projection, and
reviewed export now build and verify revision-one `ACTUAL_MINIMIZED` envelopes. The public T08
surface contains the strict aggregate projection, `candidateKey`, sanitized payload/artifact
fingerprints, and domain-separated source/reviewer HMAC seals. The legacy snapshot, its nested
source-derived hashes and aliases, root/source identifiers, report paths, URLs, endpoints, text,
and excerpts remain private. Runtime import accepts only roots plus export parameters and rebuilds
the envelope server-side; the public reviewed contract is version 2.0.0 and legacy payload fields
are rejected as unknown.

Integration owners must still:

- provision and rotate HMAC keys outside this module, authorize key use, and define stable key IDs
  and privacy domains;
- implement the retention/deletion executor and receipts across primary storage, artifacts,
  caches, and backups;
- implement independent provider live attestation and a separate certification policy;
- run a separately approved review on an actual minimized sample before making any claim about a
  real dataset.

This foundation does not complete Phase E and must not be used as evidence that Phase E or any
production review/certification gate is complete.
