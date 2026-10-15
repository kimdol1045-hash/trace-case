"""PF-SF1-T09 full-corpus LightRAG reindex safety contract.

The CLI remains preview/read-only inspection by default.  The reusable
``LedgerBackedGenerationController`` now connects an injected isolated-storage
driver to TC's durable PostgreSQL generation ledger; it receives no connection
URL or credential and performs no manual process-environment switch.

No authoritative PostgreSQL mutation is exposed by this module.  Snapshot
reads use a repeatable-read, read-only transaction and evidence contains only
identifiers, cardinalities, and SHA-256 digests, never corpus text or secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import metadata
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, unquote, urlsplit

from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.orm import Session

from tc_backend.db.models import KnowledgeEmbedding, KnowledgeNode
from tc_backend.knowledge.rebuild import (
    authoritative_ledger_digest,
    canonical_knowledge_snapshot,
)
from tc_backend.knowledge.lightrag import (
    LIGHTRAG_DISTRIBUTION,
    OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
)
from tc_backend.services.knowledge_service import node_content
from tc_backend.repositories.lightrag_generation_repository import (
    ActiveGenerationSnapshot,
    DurableLightRAGGenerationControlPlane,
)

TASK_ID = "PF-SF1-T09"
SCHEMA_VERSION = "tc-lightrag-full-reindex/v1"
PROVIDER_CONTRACT_VERSION = "tc-lightrag-generation-provider/v1"
HEAD_REVISION = "20260902_0030"
LIGHTRAG_VERSION = "1.5.6"
TARGET_CLASSES = ("disposable", "staging-copy")
PRODUCTION_TOKEN_PATTERN = re.compile(
    r"^(?:prod|production|live|primary|main)[0-9]*$"
)
SAFE_ENV_PATTERN = re.compile(r"^TC_PHASE_F_[A-Z0-9_]+$")
HEX_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
WORKSPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
GENERATION_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,94}$")
CANONICAL_SOURCE_PREFIX = "postgresql://knowledge/nodes/"
DEFAULT_MAX_DOCUMENTS = 100_000
DEFAULT_MAX_TOTAL_BYTES = 512 * 1024 * 1024
DEFAULT_BATCH_DOCUMENTS = 50
DEFAULT_BATCH_BYTES = 2 * 1024 * 1024
APPLY_UNSUPPORTED_REASON = (
    "durable control-plane/controller is implemented, but no credentialed "
    "isolated LightRAG storage driver is composed into this inspection CLI"
)


class SafetyError(RuntimeError):
    """The requested target or operation is not exactly attested."""


class VerificationError(RuntimeError):
    """A snapshot or provider generation did not match its preview."""


class LiveApplyUnsupported(RuntimeError):
    """The provider cannot supply truthful atomic activation and rollback."""


class ActivationFailure(RuntimeError):
    """Activation failed after an isolated generation was verified."""

    def __init__(
        self,
        message: str,
        *,
        rollback_attempted: bool,
        rollback_succeeded: bool,
    ) -> None:
        super().__init__(message)
        self.rollback_attempted = rollback_attempted
        self.rollback_succeeded = rollback_succeeded


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.casefold()) if token}


def _reject_production_like(value: str, label: str) -> None:
    found = sorted(
        token for token in _tokens(value) if PRODUCTION_TOKEN_PATTERN.fullmatch(token)
    )
    if found:
        raise SafetyError(
            f"{label} contains production-like token(s): {', '.join(found)}"
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_digest(value: str, label: str) -> str:
    if not HEX_DIGEST_PATTERN.fullmatch(value):
        raise SafetyError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _canonical_record_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise SafetyError("corpus record IDs must be UUIDs") from error
    canonical = str(parsed)
    if value != canonical:
        raise SafetyError("corpus record IDs must use canonical lowercase UUIDs")
    return canonical


@dataclass(frozen=True, slots=True)
class TargetDeclaration:
    name: str
    target_class: str
    authoritative_database_name: str
    auxiliary_database_name: str
    active_workspace: str

    def __post_init__(self) -> None:
        if self.target_class not in TARGET_CLASSES:
            raise SafetyError("target class must be disposable or staging-copy")
        suffix = self.target_class
        if not re.fullmatch(
            rf"tc-phase-f-(?:[a-z0-9]+-)+{re.escape(suffix)}", self.name
        ):
            raise SafetyError(f"target name must be tc-phase-f-<purpose>-{suffix}")
        _reject_production_like(self.name, "target name")
        database_suffix = suffix.replace("-", "_")
        for label, value in (
            ("authoritative database", self.authoritative_database_name),
            ("auxiliary database", self.auxiliary_database_name),
        ):
            if not re.fullmatch(
                rf"tc_phase_f_[a-z0-9]+(?:_[a-z0-9]+)*_{database_suffix}",
                value,
            ):
                raise SafetyError(
                    f"{label} name must be tc_phase_f_<purpose>_{database_suffix}"
                )
            _reject_production_like(value, f"{label} name")
        if self.authoritative_database_name == self.auxiliary_database_name:
            raise SafetyError("authoritative and auxiliary databases must be distinct")
        if not WORKSPACE_PATTERN.fullmatch(self.active_workspace):
            raise SafetyError(
                "active LightRAG workspace must be a path-safe identifier"
            )
        _reject_production_like(
            self.active_workspace,
            "active LightRAG workspace",
        )

    @property
    def authoritative_comment(self) -> str:
        return f"tc-phase-f-rehearsal:{self.target_class}:{self.name}"

    @property
    def auxiliary_comment(self) -> str:
        return f"tc-phase-f-lightrag-auxiliary:{self.target_class}:{self.name}"

    @property
    def source_target_digest(self) -> str:
        return _digest(
            {
                "class": self.target_class,
                "database": self.authoritative_database_name,
                "databaseComment": self.authoritative_comment,
                "name": self.name,
            }
        )

    @property
    def auxiliary_target_digest(self) -> str:
        # Stable authority identity: the active workspace is attested as a
        # separate pointer value and must not make consecutive generations look
        # like different auxiliary databases to the durable ledger.
        return _digest(
            {
                "class": self.target_class,
                "database": self.auxiliary_database_name,
                "databaseComment": self.auxiliary_comment,
                "name": self.name,
            }
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "class": self.target_class,
            "authoritativeDatabase": self.authoritative_database_name,
            "authoritativeTargetDigest": self.source_target_digest,
            "auxiliaryDatabase": self.auxiliary_database_name,
            "auxiliaryTargetDigest": self.auxiliary_target_digest,
            "activeWorkspace": self.active_workspace,
        }


@dataclass(frozen=True, slots=True)
class DatabaseLocation:
    sqlalchemy_url: str
    database_name: str
    host: str
    port: int

    def __repr__(self) -> str:
        return (
            f"DatabaseLocation(database_name={self.database_name!r}, "
            f"host={self.host!r}, port={self.port!r}, sqlalchemy_url='<redacted>')"
        )

    @property
    def identity(self) -> tuple[str, int, str]:
        return (self.host.casefold(), self.port, self.database_name)


def parse_database_location(
    raw_url: str,
    *,
    expected_name: str,
    target_class: str,
) -> DatabaseLocation:
    """Resolve one unambiguous PostgreSQL endpoint without exposing credentials."""

    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
        raise SafetyError("database URL must use postgresql or postgresql+psycopg")
    if not parsed.hostname or parsed.fragment:
        raise SafetyError("database URL must name one exact host without a fragment")
    if any(marker in parsed.netloc for marker in (",", " ", "\t", "\n")):
        raise SafetyError("multi-host or ambiguous database authorities are forbidden")
    actual_name = unquote(parsed.path.removeprefix("/"))
    if actual_name != expected_name or "/" in actual_name:
        raise SafetyError("database URL does not resolve to the declared database")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) - {"sslmode"} or any(len(values) != 1 for values in query.values()):
        raise SafetyError("database URL query may contain one sslmode value only")
    host = parsed.hostname
    _reject_production_like(host, "database host")
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if target_class == "disposable" and not loopback:
        raise SafetyError("disposable database targets must use loopback")
    if target_class == "staging-copy" and not loopback:
        if not (_tokens(host) & {"stage", "staging", "stg"}):
            raise SafetyError("remote staging-copy hosts must identify staging")
        if query.get("sslmode") != ["verify-full"]:
            raise SafetyError("remote staging-copy hosts require sslmode=verify-full")
    sqlalchemy_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return DatabaseLocation(
        sqlalchemy_url=sqlalchemy_url,
        database_name=expected_name,
        host=host,
        port=parsed.port or 5432,
    )


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    postgres_record_id: str
    content: str
    source_uri: str

    def __post_init__(self) -> None:
        record_id = _canonical_record_id(self.postgres_record_id)
        if not self.content.strip():
            raise SafetyError("LightRAG corpus content must not be empty")
        expected_uri = f"{CANONICAL_SOURCE_PREFIX}{record_id}"
        if self.source_uri != expected_uri:
            raise SafetyError(
                "source URI must be the canonical PostgreSQL record URI; remote URLs, "
                "redirects, and evidence permalinks are forbidden"
            )

    @property
    def content_bytes(self) -> int:
        return len(self.content.encode("utf-8"))

    @property
    def export_bytes(self) -> int:
        return self.content_bytes + len(self.source_uri.encode("utf-8")) + 36

    def attestation(self) -> dict[str, object]:
        return {
            "postgresRecordId": self.postgres_record_id,
            "contentBytes": self.content_bytes,
            "contentSha256": hashlib.sha256(self.content.encode("utf-8")).hexdigest(),
            "sourceUriSha256": hashlib.sha256(
                self.source_uri.encode("utf-8")
            ).hexdigest(),
        }


def corpus_digest(documents: Iterable[CorpusDocument]) -> str:
    records = [document.attestation() for document in documents]
    return _digest(sorted(records, key=lambda item: str(item["postgresRecordId"])))


@dataclass(frozen=True, slots=True)
class CorpusSnapshot:
    source_target_digest: str
    source_ledger_digest: str
    projection_digest: str
    pgvector_digest: str
    pgvector_model: str
    pgvector_dimension: int
    documents: tuple[CorpusDocument, ...]

    def __post_init__(self) -> None:
        for label, value in (
            ("source target digest", self.source_target_digest),
            ("source ledger digest", self.source_ledger_digest),
            ("projection digest", self.projection_digest),
            ("pgvector digest", self.pgvector_digest),
        ):
            _required_digest(value, label)
        ordered = tuple(
            sorted(self.documents, key=lambda document: document.postgres_record_id)
        )
        if not ordered:
            raise SafetyError("full LightRAG corpus snapshot must not be empty")
        if ordered != self.documents:
            raise SafetyError("corpus documents must be sorted by PostgreSQL record ID")
        if len({document.postgres_record_id for document in ordered}) != len(ordered):
            raise SafetyError("corpus documents must have unique PostgreSQL record IDs")
        if self.pgvector_dimension != 1024:
            raise SafetyError("pgvector projection must use 1024-dimensional BGE-M3")
        if self.pgvector_model != "bge-m3":
            raise SafetyError(
                "pgvector projection model version must be exactly bge-m3"
            )

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def total_content_bytes(self) -> int:
        return sum(document.content_bytes for document in self.documents)

    @property
    def corpus_digest(self) -> str:
        return corpus_digest(self.documents)

    def public_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "sourceTargetDigest": self.source_target_digest,
            "sourceLedgerDigest": self.source_ledger_digest,
            "projectionDigest": self.projection_digest,
            "pgvectorDigest": self.pgvector_digest,
            "pgvectorModel": self.pgvector_model,
            "pgvectorDimension": self.pgvector_dimension,
            "documentCount": self.document_count,
            "totalContentBytes": self.total_content_bytes,
            "corpusDigest": self.corpus_digest,
            "rawCorpusOmitted": True,
        }


@dataclass(frozen=True, slots=True)
class ExportPolicy:
    max_documents: int = DEFAULT_MAX_DOCUMENTS
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    batch_documents: int = DEFAULT_BATCH_DOCUMENTS
    batch_bytes: int = DEFAULT_BATCH_BYTES

    def __post_init__(self) -> None:
        for label, value in (
            ("max_documents", self.max_documents),
            ("max_total_bytes", self.max_total_bytes),
            ("batch_documents", self.batch_documents),
            ("batch_bytes", self.batch_bytes),
        ):
            if isinstance(value, bool) or value < 1:
                raise SafetyError(f"{label} must be positive")
        if self.batch_documents > self.max_documents:
            raise SafetyError("batch document limit cannot exceed corpus limit")
        if self.batch_bytes > self.max_total_bytes:
            raise SafetyError("batch byte limit cannot exceed corpus byte limit")


def bounded_batches(
    snapshot: CorpusSnapshot, policy: ExportPolicy
) -> tuple[tuple[CorpusDocument, ...], ...]:
    if snapshot.document_count > policy.max_documents:
        raise SafetyError("source corpus exceeds the configured document bound")
    export_bytes = sum(document.export_bytes for document in snapshot.documents)
    if export_bytes > policy.max_total_bytes:
        raise SafetyError("source corpus exceeds the configured byte bound")
    batches: list[tuple[CorpusDocument, ...]] = []
    current: list[CorpusDocument] = []
    current_bytes = 0
    for document in snapshot.documents:
        if document.export_bytes > policy.batch_bytes:
            raise SafetyError("one corpus document exceeds the batch byte bound")
        if current and (
            len(current) >= policy.batch_documents
            or current_bytes + document.export_bytes > policy.batch_bytes
        ):
            batches.append(tuple(current))
            current = []
            current_bytes = 0
        current.append(document)
        current_bytes += document.export_bytes
    if current:
        batches.append(tuple(current))
    return tuple(batches)


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    contract_version: str
    provider_id: str
    auxiliary_target_digest: str
    control_plane: str
    redirect_hops: int
    fresh_generation_isolation: bool
    atomic_compare_and_swap_activation: bool
    atomic_compare_and_swap_rollback: bool


@dataclass(frozen=True, slots=True)
class GenerationInspection:
    generation_id: str
    document_count: int
    processed_count: int
    failed_count: int
    corpus_digest: str
    processed_document_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IsolatedGenerationStorageCapabilities:
    """Credential-free exact-target attestation returned by the storage driver."""

    authority_id: str
    provider_id: str
    provider_contract_version: str
    provider_distribution: str
    provider_version: str
    auxiliary_target_digest: str
    runtime_config_digest: str
    control_plane: str
    redirect_hops: int
    fresh_generation_isolation: bool


@dataclass(frozen=True, slots=True)
class IsolatedGenerationInspection:
    """Storage evidence bound to the fresh workspace and preview manifest."""

    generation_id: str
    workspace: str
    auxiliary_target_digest: str
    manifest_digest: str
    document_count: int
    processed_count: int
    failed_count: int
    corpus_digest: str
    processed_document_ids: tuple[str, ...]


def _exact_identity_set_matches(
    actual_ids: Sequence[str], expected_ids: set[str]
) -> bool:
    actual_id_set = set(actual_ids)
    return (
        len(actual_ids) == len(expected_ids)
        and len(actual_id_set) == len(actual_ids)
        and actual_id_set == expected_ids
    )


class SourceSnapshotReader(Protocol):
    def snapshot(self) -> CorpusSnapshot: ...


class GenerationProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...

    def generation_exists(self, generation_id: str) -> bool: ...

    def begin_generation(self, generation_id: str, manifest_digest: str) -> None: ...

    def ingest_batch(
        self, generation_id: str, documents: Sequence[CorpusDocument]
    ) -> None: ...

    def inspect_generation(self, generation_id: str) -> GenerationInspection: ...

    def discard_generation(self, generation_id: str) -> None: ...

    def current_generation(self) -> str | None: ...

    def activate_generation(
        self, generation_id: str, *, expected_previous: str | None
    ) -> None: ...

    def rollback_generation(
        self,
        *,
        expected_current: str,
        restore_generation: str | None,
    ) -> None: ...


class IsolatedGenerationStorage(Protocol):
    """Injected credential-owning storage boundary; controller sees only operations."""

    def capabilities(self) -> IsolatedGenerationStorageCapabilities: ...

    def generation_exists(self, generation_id: str) -> bool: ...

    def begin_generation(
        self,
        generation_id: str,
        *,
        workspace: str,
        manifest_digest: str,
    ) -> None: ...

    def ingest_batch(
        self, generation_id: str, documents: Sequence[CorpusDocument]
    ) -> None: ...

    def inspect_generation(
        self, generation_id: str
    ) -> IsolatedGenerationInspection: ...

    def discard_generation(self, generation_id: str) -> None: ...


@runtime_checkable
class TransitionEvidenceProvider(Protocol):
    def transition_evidence(self) -> dict[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class ReindexPlan:
    target: TargetDeclaration
    snapshot: CorpusSnapshot
    policy: ExportPolicy
    manifest_digest: str
    generation_id: str
    confirmation_required: str

    @classmethod
    def create(
        cls,
        *,
        target: TargetDeclaration,
        snapshot: CorpusSnapshot,
        policy: ExportPolicy | None = None,
        generation_prefix: str = "tc_lrag",
    ) -> ReindexPlan:
        selected_policy = policy or ExportPolicy()
        bounded_batches(snapshot, selected_policy)
        if snapshot.source_target_digest != target.source_target_digest:
            raise VerificationError(
                "snapshot belongs to a different authoritative target"
            )
        manifest_payload = {
            "schemaVersion": SCHEMA_VERSION,
            "providerContractVersion": PROVIDER_CONTRACT_VERSION,
            "target": target.public_dict(),
            "snapshot": snapshot.public_dict(),
            "policy": {
                "maxDocuments": selected_policy.max_documents,
                "maxTotalBytes": selected_policy.max_total_bytes,
                "batchDocuments": selected_policy.batch_documents,
                "batchBytes": selected_policy.batch_bytes,
            },
        }
        manifest_digest = _digest(manifest_payload)
        # Keep every isolated workspace below LightRAG/PostgreSQL identifier
        # bounds even after many generations; the manifest already includes
        # the observed active workspace and all source evidence.
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,45}", generation_prefix):
            raise SafetyError("generation prefix is not path-safe or is too long")
        separator = "_" if generation_prefix == "tc_lrag" else "-"
        # Custom rehearsal prefixes can already be fairly long.  Twelve
        # hexadecimal characters still bind the generation to the canonical
        # manifest while leaving room for LightRAG's longest AGE label suffix
        # inside PostgreSQL's 63-byte identifier limit.
        digest_length = 24 if generation_prefix == "tc_lrag" else 12
        generation_id = (
            f"{generation_prefix}{separator}{manifest_digest[:digest_length]}"
        )
        if not GENERATION_PATTERN.fullmatch(generation_id):
            raise SafetyError("derived generation ID is not path-safe")
        confirmation = f"RUN {TASK_ID} LIGHTRAG-FULL-REINDEX {manifest_digest}"
        return cls(
            target=target,
            snapshot=snapshot,
            policy=selected_policy,
            manifest_digest=manifest_digest,
            generation_id=generation_id,
            confirmation_required=confirmation,
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mode": "preview",
            "executionStatus": "NOT_RUN",
            "target": self.target.public_dict(),
            "snapshot": self.snapshot.public_dict(),
            "manifestDigest": self.manifest_digest,
            "generationId": self.generation_id,
            "confirmationRequired": self.confirmation_required,
            "authoritativeLedgerMutation": False,
            "rawCorpusOmitted": True,
        }


def _assert_same_snapshot(expected: CorpusSnapshot, actual: CorpusSnapshot) -> None:
    comparisons = (
        ("source target", expected.source_target_digest, actual.source_target_digest),
        ("source ledger", expected.source_ledger_digest, actual.source_ledger_digest),
        ("projection", expected.projection_digest, actual.projection_digest),
        ("pgvector", expected.pgvector_digest, actual.pgvector_digest),
        ("corpus", expected.corpus_digest, actual.corpus_digest),
        ("cardinality", expected.document_count, actual.document_count),
    )
    stale = [label for label, before, after in comparisons if before != after]
    if stale:
        raise VerificationError(
            "source snapshot changed after preview: " + ", ".join(stale)
        )


def _validate_provider(capabilities: ProviderCapabilities, plan: ReindexPlan) -> None:
    if capabilities.contract_version != PROVIDER_CONTRACT_VERSION:
        raise LiveApplyUnsupported("provider contract version is unsupported")
    if capabilities.auxiliary_target_digest != plan.target.auxiliary_target_digest:
        raise VerificationError("provider belongs to a different auxiliary target")
    if (
        capabilities.control_plane != "embedded-postgresql"
        or capabilities.redirect_hops != 0
    ):
        raise LiveApplyUnsupported(
            "redirecting, HTTP, or ambiguous remote provider control planes are forbidden"
        )
    if not capabilities.fresh_generation_isolation:
        raise LiveApplyUnsupported(
            "provider cannot create an isolated fresh generation"
        )
    if not capabilities.atomic_compare_and_swap_activation:
        raise LiveApplyUnsupported("provider lacks atomic compare-and-swap activation")
    if not capabilities.atomic_compare_and_swap_rollback:
        raise LiveApplyUnsupported("provider lacks atomic compare-and-swap rollback")


class LedgerBackedGenerationController:
    """Credential-free controller for isolated storage plus the durable TC ledger.

    Connection URLs, provider tokens, and process environment are intentionally
    absent from this constructor.  The storage driver owns its already-created
    handle, while all reader routing is resolved through the injected durable
    PostgreSQL control plane.
    """

    def __init__(
        self,
        *,
        plan: ReindexPlan,
        source: SourceSnapshotReader,
        storage: IsolatedGenerationStorage,
        control_plane: DurableLightRAGGenerationControlPlane,
        actor_id: str,
    ) -> None:
        if not actor_id.strip() or len(actor_id) > 256:
            raise SafetyError("controller actor ID must be non-empty and bounded")
        self._plan = plan
        self._source = source
        self._storage = storage
        self._control_plane = control_plane
        self._actor_id = actor_id
        self._inspection: IsolatedGenerationInspection | None = None
        self._observed_pointer: ActiveGenerationSnapshot | None = None
        self._activation_event: object | None = None
        self._rollback_event: object | None = None

    def capabilities(self) -> ProviderCapabilities:
        storage = self._validated_storage_capabilities()
        return ProviderCapabilities(
            contract_version=PROVIDER_CONTRACT_VERSION,
            provider_id=storage.provider_id,
            auxiliary_target_digest=storage.auxiliary_target_digest,
            control_plane="embedded-postgresql",
            redirect_hops=0,
            fresh_generation_isolation=True,
            atomic_compare_and_swap_activation=True,
            atomic_compare_and_swap_rollback=True,
        )

    def generation_exists(self, generation_id: str) -> bool:
        self._exact_generation(generation_id)
        return self._storage.generation_exists(generation_id)

    def begin_generation(self, generation_id: str, manifest_digest: str) -> None:
        self._exact_generation(generation_id)
        if manifest_digest != self._plan.manifest_digest:
            raise VerificationError("generation manifest differs from the preview")
        self._validated_storage_capabilities()
        if not WORKSPACE_PATTERN.fullmatch(generation_id):
            raise SafetyError("fresh generation ID is not a valid isolated workspace")
        if self._storage.generation_exists(generation_id):
            raise SafetyError("fresh generation workspace already exists")
        self._storage.begin_generation(
            generation_id,
            workspace=generation_id,
            manifest_digest=manifest_digest,
        )
        self._inspection = None

    def ingest_batch(
        self, generation_id: str, documents: Sequence[CorpusDocument]
    ) -> None:
        self._exact_generation(generation_id)
        self._storage.ingest_batch(generation_id, documents)

    def inspect_generation(self, generation_id: str) -> GenerationInspection:
        self._exact_generation(generation_id)
        inspection = self._storage.inspect_generation(generation_id)
        self._verify_inspection_binding(inspection)
        self._inspection = inspection
        return GenerationInspection(
            generation_id=inspection.generation_id,
            document_count=inspection.document_count,
            processed_count=inspection.processed_count,
            failed_count=inspection.failed_count,
            corpus_digest=inspection.corpus_digest,
            processed_document_ids=inspection.processed_document_ids,
        )

    def discard_generation(self, generation_id: str) -> None:
        self._exact_generation(generation_id)
        self._storage.discard_generation(generation_id)
        self._inspection = None

    def current_generation(self) -> str | None:
        self._observed_pointer = self._control_plane.active_snapshot()
        return self._observed_pointer.generation_id

    def activate_generation(
        self, generation_id: str, *, expected_previous: str | None
    ) -> None:
        self._exact_generation(generation_id)
        inspection = self._inspection
        if inspection is None:
            raise VerificationError("generation has no controller-verified inspection")
        self._verify_inspection_binding(inspection)
        self._verify_complete_inspection(inspection)
        observed = self._observed_pointer
        if observed is None or observed.generation_id != expected_previous:
            raise VerificationError("active-generation observation is missing or stale")
        _assert_same_snapshot(self._plan.snapshot, self._source.snapshot())
        storage = self._validated_storage_capabilities()
        expected_ids = sorted(
            document.postgres_record_id for document in self._plan.snapshot.documents
        )
        self._control_plane.register_generation(
            generation_id=generation_id,
            provider_id=storage.provider_id,
            workspace=generation_id,
            auxiliary_target_digest=self._plan.target.auxiliary_target_digest,
            manifest_digest=self._plan.manifest_digest,
            corpus_digest=self._plan.snapshot.corpus_digest,
            source_ledger_digest=self._plan.snapshot.source_ledger_digest,
            projection_digest=self._plan.snapshot.projection_digest,
            pgvector_digest=self._plan.snapshot.pgvector_digest,
            document_count=self._plan.snapshot.document_count,
            recorded_by=self._actor_id,
            idempotency_key=f"t09-register-{self._plan.manifest_digest}",
            metadata={
                "auxiliaryAuthorityId": storage.authority_id,
                "providerContractVersion": PROVIDER_CONTRACT_VERSION,
                "providerDistribution": storage.provider_distribution,
                "providerVersion": storage.provider_version,
                "runtimeConfigDigest": storage.runtime_config_digest,
                "sourceTargetDigest": self._plan.snapshot.source_target_digest,
                "processedIdentitySetDigest": _digest(expected_ids),
                "citationPolicy": "POSTGRESQL_CANONICAL_NODE_URI",
                "rawCorpusOmitted": True,
            },
        )
        self._activation_event = self._control_plane.activate(
            generation_id,
            expected_generation_id=expected_previous,
            expected_pointer_version=observed.pointer_version,
            actor_id=self._actor_id,
            idempotency_key=f"t09-activate-{self._plan.manifest_digest}",
        )
        self._after_activation()

    def rollback_generation(
        self,
        *,
        expected_current: str,
        restore_generation: str | None,
    ) -> None:
        self._exact_generation(expected_current)
        if restore_generation is None:
            raise VerificationError(
                "durable rollback requires an activated predecessor"
            )
        if not GENERATION_PATTERN.fullmatch(restore_generation):
            raise SafetyError("rollback generation ID is not path-safe")
        observed = self._control_plane.active_snapshot()
        if observed.generation_id != expected_current:
            raise VerificationError("rollback expected-current pointer is stale")
        self._before_rollback()
        self._rollback_event = self._control_plane.rollback(
            restore_generation,
            expected_generation_id=expected_current,
            expected_pointer_version=observed.pointer_version,
            actor_id=self._actor_id,
            idempotency_key=f"t09-rollback-{self._plan.manifest_digest}",
        )

    def transition_evidence(self) -> dict[str, object] | None:
        event = self._rollback_event or self._activation_event
        if event is None:
            return None
        return {
            "authority": "TC_POSTGRESQL_GENERATION_LEDGER",
            "operation": str(getattr(event, "operation")),
            "outcome": str(getattr(event, "outcome")),
            "eventId": str(getattr(event, "id")),
            "receiptDigest": str(getattr(event, "receipt_digest")),
            "committedPointerVersion": int(getattr(event, "committed_pointer_version")),
            "manualEnvironmentSwitch": False,
            "credentialsOmitted": True,
        }

    def _validated_storage_capabilities(
        self,
    ) -> IsolatedGenerationStorageCapabilities:
        capabilities = self._storage.capabilities()
        if capabilities.authority_id != self._plan.target.name:
            raise VerificationError("storage authority identity differs from the preview")
        if capabilities.provider_id != OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID:
            raise LiveApplyUnsupported("storage provider identity is unsupported")
        if (
            capabilities.provider_contract_version != PROVIDER_CONTRACT_VERSION
            or capabilities.provider_distribution != LIGHTRAG_DISTRIBUTION
            or capabilities.provider_version != LIGHTRAG_VERSION
        ):
            raise LiveApplyUnsupported(
                "storage provider contract, distribution, or version is unsupported"
            )
        if not re.fullmatch(
            r"(?:sha256:)?[a-f0-9]{64}", capabilities.runtime_config_digest
        ):
            raise VerificationError("storage runtime configuration digest is invalid")
        if (
            capabilities.auxiliary_target_digest
            != self._plan.target.auxiliary_target_digest
        ):
            raise VerificationError("storage belongs to a different auxiliary target")
        if (
            capabilities.control_plane != "embedded-postgresql"
            or capabilities.redirect_hops != 0
        ):
            raise LiveApplyUnsupported(
                "storage target is remote, redirectable, or not embedded PostgreSQL"
            )
        if not capabilities.fresh_generation_isolation:
            raise LiveApplyUnsupported(
                "storage cannot create a fresh isolated generation"
            )
        return capabilities

    def _verify_inspection_binding(
        self, inspection: IsolatedGenerationInspection
    ) -> None:
        if (
            inspection.generation_id != self._plan.generation_id
            or inspection.workspace != self._plan.generation_id
            or inspection.auxiliary_target_digest
            != self._plan.target.auxiliary_target_digest
            or inspection.manifest_digest != self._plan.manifest_digest
        ):
            raise VerificationError(
                "isolated generation inspection is not bound to the exact preview target"
            )

    def _verify_complete_inspection(
        self, inspection: IsolatedGenerationInspection
    ) -> None:
        expected_ids = {
            document.postgres_record_id for document in self._plan.snapshot.documents
        }
        expected_count = self._plan.snapshot.document_count
        if (
            inspection.document_count != expected_count
            or inspection.processed_count != expected_count
            or inspection.failed_count != 0
            or not _exact_identity_set_matches(
                inspection.processed_document_ids, expected_ids
            )
            or inspection.corpus_digest != self._plan.snapshot.corpus_digest
        ):
            raise VerificationError(
                "controller refuses incomplete or checksum-mismatched generation evidence"
            )

    def _exact_generation(self, generation_id: str) -> None:
        if generation_id != self._plan.generation_id:
            raise VerificationError("generation ID differs from the preview")

    def _after_activation(self) -> None:
        """Fault-injection seam after the durable CAS commit."""

    def _before_rollback(self) -> None:
        """Fault-injection seam before durable rollback."""


class FullReindexWorkflow:
    """Provider-neutral full reindex with pre/post source and activation checks."""

    def __init__(self, source: SourceSnapshotReader) -> None:
        self._source = source

    def apply(
        self,
        plan: ReindexPlan,
        provider: GenerationProvider,
        *,
        confirmation: str,
        expected_manifest_digest: str,
    ) -> dict[str, object]:
        if confirmation != plan.confirmation_required:
            raise SafetyError("full reindex confirmation does not match the preview")
        if expected_manifest_digest != plan.manifest_digest:
            raise VerificationError("manifest digest does not match the preview")
        _assert_same_snapshot(plan.snapshot, self._source.snapshot())
        _validate_provider(provider.capabilities(), plan)
        if provider.generation_exists(plan.generation_id):
            raise SafetyError("derived generation already exists; refusing stale reuse")

        began = False
        activated = False
        previous: str | None = None
        try:
            provider.begin_generation(plan.generation_id, plan.manifest_digest)
            began = True
            for batch in bounded_batches(plan.snapshot, plan.policy):
                provider.ingest_batch(plan.generation_id, batch)
            inspection = provider.inspect_generation(plan.generation_id)
            expected_count = plan.snapshot.document_count
            expected_ids = {
                document.postgres_record_id for document in plan.snapshot.documents
            }
            if (
                inspection.generation_id != plan.generation_id
                or inspection.document_count != expected_count
                or inspection.processed_count != expected_count
                or inspection.failed_count != 0
                or not _exact_identity_set_matches(
                    inspection.processed_document_ids, expected_ids
                )
            ):
                raise VerificationError(
                    "isolated LightRAG generation cardinality/status is incomplete"
                )
            if inspection.corpus_digest != plan.snapshot.corpus_digest:
                raise VerificationError(
                    "isolated LightRAG generation checksum does not match source corpus"
                )
            _assert_same_snapshot(plan.snapshot, self._source.snapshot())
            previous = provider.current_generation()
            try:
                provider.activate_generation(
                    plan.generation_id, expected_previous=previous
                )
                activated = True
                if provider.current_generation() != plan.generation_id:
                    raise RuntimeError(
                        "activation did not publish the verified generation"
                    )
            except Exception as error:
                rollback_attempted = False
                rollback_succeeded = False
                try:
                    current = provider.current_generation()
                    if current == plan.generation_id:
                        rollback_attempted = True
                        provider.rollback_generation(
                            expected_current=plan.generation_id,
                            restore_generation=previous,
                        )
                        rollback_succeeded = provider.current_generation() == previous
                    elif current == previous:
                        rollback_succeeded = True
                except Exception:
                    rollback_succeeded = False
                raise ActivationFailure(
                    f"LightRAG activation failed: {error.__class__.__name__}",
                    rollback_attempted=rollback_attempted,
                    rollback_succeeded=rollback_succeeded,
                ) from None
            report: dict[str, object] = {
                "schemaVersion": SCHEMA_VERSION,
                "mode": "apply",
                "executionStatus": "SUCCEEDED",
                "manifestDigest": plan.manifest_digest,
                "generationId": plan.generation_id,
                "previousGenerationId": previous,
                "documentCount": expected_count,
                "corpusDigest": plan.snapshot.corpus_digest,
                "sourceLedgerDigest": plan.snapshot.source_ledger_digest,
                "projectionDigest": plan.snapshot.projection_digest,
                "pgvectorDigest": plan.snapshot.pgvector_digest,
                "activation": "ATOMIC_COMPARE_AND_SWAP",
                "authoritativeLedgerMutation": False,
                "rawCorpusOmitted": True,
            }
            if isinstance(provider, TransitionEvidenceProvider):
                transition = provider.transition_evidence()
                if transition is not None:
                    report["controlPlane"] = transition
            return report
        except ActivationFailure as error:
            if (
                began
                and not activated
                and not error.rollback_attempted
                and error.rollback_succeeded
            ):
                # The pointer is provably unchanged, so this was a
                # pre-activation failure and the isolated workspace is safe to
                # discard.  Ambiguous/post-CAS failures retain it for evidence.
                provider.discard_generation(plan.generation_id)
            raise
        except Exception:
            if began and not activated:
                provider.discard_generation(plan.generation_id)
            raise


def _vector_digest(values: Sequence[float]) -> str:
    return _digest([format(float(value), ".17g") for value in values])


class PostgresSnapshotReader:
    """Read the authoritative projection with a non-writing database role."""

    def __init__(
        self,
        engine: Engine,
        *,
        target: TargetDeclaration,
    ) -> None:
        self._engine = engine
        self._target = target

    def snapshot(self) -> CorpusSnapshot:
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE"
                )
                _assert_database(
                    connection,
                    expected_name=self._target.authoritative_database_name,
                    expected_comment=self._target.authoritative_comment,
                    expected_revision=HEAD_REVISION,
                    require_extensions=("vector",),
                )
                session = Session(bind=connection)
                source_digest = authoritative_ledger_digest(session)
                projection = canonical_knowledge_snapshot(session)
                nodes = tuple(
                    session.scalars(select(KnowledgeNode).order_by(KnowledgeNode.id))
                )
                documents = tuple(
                    CorpusDocument(
                        postgres_record_id=str(node.id),
                        content=node_content(node),
                        source_uri=f"{CANONICAL_SOURCE_PREFIX}{node.id}",
                    )
                    for node in nodes
                )
                embeddings = tuple(
                    session.scalars(
                        select(KnowledgeEmbedding)
                        .where(
                            KnowledgeEmbedding.owner_type == "NODE",
                            KnowledgeEmbedding.model == "bge-m3",
                            KnowledgeEmbedding.model_version == "bge-m3",
                        )
                        .order_by(KnowledgeEmbedding.owner_id, KnowledgeEmbedding.id)
                    )
                )
                by_owner: dict[uuid.UUID, list[KnowledgeEmbedding]] = {}
                for embedding in embeddings:
                    by_owner.setdefault(embedding.owner_id, []).append(embedding)
                vector_rows: list[dict[str, object]] = []
                for node, document in zip(nodes, documents, strict=True):
                    expected_content_hash = str(document.attestation()["contentSha256"])
                    matching = [
                        embedding
                        for embedding in by_owner.get(node.id, [])
                        if embedding.content_hash == expected_content_hash
                    ]
                    if len(matching) != 1:
                        raise VerificationError(
                            "pgvector projection is partial, stale, or ambiguous for the corpus"
                        )
                    embedding = matching[0]
                    vector_rows.append(
                        {
                            "postgresRecordId": str(node.id),
                            "contentSha256": embedding.content_hash,
                            "model": embedding.model,
                            "modelVersion": embedding.model_version,
                            "vectorSha256": _vector_digest(embedding.embedding),
                        }
                    )
                if len(vector_rows) != len(documents):
                    raise VerificationError(
                        "pgvector cardinality does not match the LightRAG corpus"
                    )
                after_source_digest = authoritative_ledger_digest(session)
                if after_source_digest != source_digest:
                    raise VerificationError(
                        "authoritative ledger changed during snapshot"
                    )
                transaction.commit()
                return CorpusSnapshot(
                    source_target_digest=self._target.source_target_digest,
                    source_ledger_digest=source_digest,
                    projection_digest=projection.digest,
                    pgvector_digest=_digest(vector_rows),
                    pgvector_model="bge-m3",
                    pgvector_dimension=1024,
                    documents=documents,
                )
            except Exception:
                transaction.rollback()
                raise


def _assert_database(
    connection: Connection,
    *,
    expected_name: str,
    expected_comment: str,
    expected_revision: str | None,
    require_extensions: Sequence[str],
) -> dict[str, object]:
    identity = connection.execute(
        text(
            "SELECT current_database(), current_setting('server_version_num'), "
            "COALESCE(shobj_description(oid, 'pg_database'), '') "
            "FROM pg_database WHERE datname = current_database()"
        )
    ).one()
    if str(identity[0]) != expected_name:
        raise SafetyError("connected database is not the declared exact target")
    if int(identity[1]) < 160_000:
        raise SafetyError("PF-SF1-T09 requires PostgreSQL 16 or newer")
    if str(identity[2]) != expected_comment:
        raise SafetyError("database comment attestation mismatch")
    extensions = {
        str(row[0])
        for row in connection.execute(
            text(
                "SELECT extname FROM pg_extension "
                "WHERE extname = ANY(CAST(:names AS text[]))"
            ),
            {"names": list(require_extensions)},
        )
    }
    if extensions != set(require_extensions):
        raise SafetyError("database extensions do not match the declared role")
    revision: str | None = None
    if expected_revision is not None:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        if revision != expected_revision:
            raise SafetyError(
                "authoritative database is not at the required head revision"
            )
    return {
        "databaseName": expected_name,
        "databaseCommentAttested": True,
        "serverVersionNum": int(identity[1]),
        "extensions": sorted(extensions),
        "alembicRevision": revision,
    }


def inspect_auxiliary(
    engine: Engine,
    *,
    target: TargetDeclaration,
    expected_snapshot: CorpusSnapshot,
) -> dict[str, object]:
    """Inspect the active workspace without claiming it can be activated."""

    required_tables = (
        "lightrag_doc_full",
        "lightrag_doc_chunks",
        "lightrag_doc_status",
        "lightrag_vdb_chunks",
        "lightrag_vdb_entity",
        "lightrag_vdb_relation",
    )
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE"
            )
            database = _assert_database(
                connection,
                expected_name=target.auxiliary_database_name,
                expected_comment=target.auxiliary_comment,
                expected_revision=None,
                require_extensions=("age", "vector"),
            )
            present = {
                name
                for name in required_tables
                if connection.execute(
                    text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name}
                ).scalar_one()
            }
            if present != set(required_tables):
                transaction.commit()
                return {
                    **database,
                    "workspace": target.active_workspace,
                    "state": "ABSENT_OR_PARTIAL_SCHEMA",
                    "requiredTableCount": len(required_tables),
                    "presentTableCount": len(present),
                    "matchesSourceSnapshot": False,
                    "rawCorpusOmitted": True,
                }
            full_rows = connection.execute(
                text(
                    "SELECT id, content FROM lightrag_doc_full "
                    "WHERE workspace = :workspace ORDER BY id"
                ),
                {"workspace": target.active_workspace},
            ).all()
            status_rows = connection.execute(
                text(
                    "SELECT id, status FROM lightrag_doc_status "
                    "WHERE workspace = :workspace ORDER BY id"
                ),
                {"workspace": target.active_workspace},
            ).all()
            chunk_rows = connection.execute(
                text(
                    "SELECT full_doc_id, file_path FROM lightrag_doc_chunks "
                    "WHERE workspace = :workspace ORDER BY full_doc_id, id"
                ),
                {"workspace": target.active_workspace},
            ).all()
            vector_chunk_rows = connection.execute(
                text(
                    "SELECT full_doc_id FROM lightrag_vdb_chunks "
                    "WHERE workspace = :workspace ORDER BY full_doc_id, id"
                ),
                {"workspace": target.active_workspace},
            ).all()
            graph_name = (
                "chunk_entity_relation"
                if target.active_workspace.casefold() == "default"
                else f"{target.active_workspace}_chunk_entity_relation"
            )
            graph_present = bool(
                connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph "
                        "WHERE name = :graph_name)"
                    ),
                    {"graph_name": graph_name},
                ).scalar_one()
            )
            citations: dict[str, set[str]] = {}
            for full_doc_id, file_path in chunk_rows:
                citations.setdefault(str(full_doc_id), set()).add(str(file_path or ""))
            actual_documents: list[CorpusDocument] = []
            citation_matches = True
            for raw_id, content in full_rows:
                record_id = str(raw_id)
                expected_uri = f"{CANONICAL_SOURCE_PREFIX}{record_id}"
                if citations.get(record_id) != {expected_uri}:
                    citation_matches = False
                try:
                    if not isinstance(content, str):
                        raise SafetyError("LightRAG full document content must be text")
                    actual_documents.append(
                        CorpusDocument(record_id, content, expected_uri)
                    )
                except SafetyError:
                    citation_matches = False
            expected_ids = {
                document.postgres_record_id for document in expected_snapshot.documents
            }
            status_entries = tuple(
                (str(row_id), str(status)) for row_id, status in status_rows
            )
            status_ids = tuple(row_id for row_id, _status in status_entries)
            processed_count = sum(
                status == "processed" for _row_id, status in status_entries
            )
            status_identity_matches = _exact_identity_set_matches(
                status_ids, expected_ids
            )
            actual_digest = corpus_digest(actual_documents)
            document_chunk_ids = set(citations)
            vector_chunk_ids = {str(row[0]) for row in vector_chunk_rows}
            chunk_cardinality_matches = (
                document_chunk_ids == expected_ids and vector_chunk_ids == expected_ids
            )
            matches = (
                len(actual_documents) == expected_snapshot.document_count
                and status_identity_matches
                and processed_count == expected_snapshot.document_count
                and actual_digest == expected_snapshot.corpus_digest
                and citation_matches
                and chunk_cardinality_matches
                and graph_present
            )
            transaction.commit()
            return {
                **database,
                "workspace": target.active_workspace,
                "state": "INSPECTED",
                "documentCount": len(actual_documents),
                "statusCount": len(status_entries),
                "processedCount": processed_count,
                "statusDocumentIdentitySetMatches": status_identity_matches,
                "documentChunkCount": len(chunk_rows),
                "vectorChunkCount": len(vector_chunk_rows),
                "chunkDocumentCardinalityMatches": chunk_cardinality_matches,
                "corpusDigest": actual_digest,
                "citationChecksumMatches": citation_matches,
                "graphNameSha256": hashlib.sha256(
                    graph_name.encode("utf-8")
                ).hexdigest(),
                "graphPresent": graph_present,
                "matchesSourceSnapshot": matches,
                "rawCorpusOmitted": True,
            }
        except Exception:
            transaction.rollback()
            raise


def current_adapter_capability() -> dict[str, object]:
    try:
        installed = metadata.version("lightrag-hku")
    except metadata.PackageNotFoundError:
        installed = "NOT_INSTALLED"
    return {
        "provider": OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
        "installedVersion": installed,
        "requiredVersion": LIGHTRAG_VERSION,
        "storage": [
            "PGKVStorage",
            "PGVectorStorage",
            "PGDocStatusStorage",
            "PGGraphStorage",
        ],
        "generationIsolation": "DEDICATED_SUBPROCESS_PER_GENERATION",
        "storageNamespace": "POSTGRES_WORKSPACE",
        "controller": "LedgerBackedGenerationController",
        "atomicActivation": "TC_POSTGRESQL_CAS",
        "atomicRollback": "TC_POSTGRESQL_CAS",
        "durableActiveGenerationPointer": True,
        "runtimeResolver": "LightRAGRuntimeResolver",
        "runtimeResolverAvailable": True,
        "runtimeFactory": "IsolatedLightRAGGenerationRuntimeFactory",
        "runtimeFactoryAvailable": True,
        "runtimeFactoryConfigured": False,
        "runtimeSettingsComposition": "API_AND_WORKER_OPT_IN",
        "runtimeDefaultEnabled": False,
        "approvedBootstrapAvailable": False,
        "stagingCredentialsConfigured": False,
        "runtimeProcessEnvironment": "CHILD_ONLY_IMMUTABLE_POSTGRES_ENVIRONMENT",
        "parentProcessEnvironmentMutation": "FORBIDDEN",
        "runtimeCacheKey": [
            "pointerVersion",
            "generationId",
            "registrationDigest",
        ],
        "runtimePointerAttestation": "PER_OPERATION_BEFORE_AND_AFTER",
        "manualRuntimeEnvironmentSwitch": "FORBIDDEN",
        "applicationRuntimeComposition": True,
        "liveApplySupported": False,
        "executionStatus": "NOT_RUN",
    }


def _required_environment(name: str) -> str:
    if not SAFE_ENV_PATTERN.fullmatch(name):
        raise SafetyError("database URL environment names must start with TC_PHASE_F_")
    value = os.getenv(name)
    if not value:
        raise SafetyError(f"required environment variable is not set: {name}")
    return value


def _inspection_confirmation(target: TargetDeclaration) -> str:
    return f"INSPECT {TASK_ID} LIGHTRAG-FULL-REINDEX ON {target.name}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview or read-only inspect the PF-SF1-T09 LightRAG reindex boundary."
    )
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--target-class", required=True, choices=TARGET_CLASSES)
    parser.add_argument("--authoritative-database-name", required=True)
    parser.add_argument("--authoritative-database-url-env", required=True)
    parser.add_argument("--auxiliary-database-name", required=True)
    parser.add_argument("--auxiliary-database-url-env", required=True)
    parser.add_argument("--active-workspace", default="tc")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--confirm")
    return parser


def _preview(target: TargetDeclaration) -> dict[str, object]:
    return {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "mode": "preview",
        "task": TASK_ID,
        "executionStatus": "NOT_RUN",
        "executes": False,
        "target": target.public_dict(),
        "confirmationRequired": _inspection_confirmation(target),
        "reads": [
            "attested authoritative PostgreSQL ledger and pgvector projection",
            "attested auxiliary LightRAG PostgreSQL active workspace",
        ],
        "mutates": [],
        "exportBounds": {
            "maxDocuments": DEFAULT_MAX_DOCUMENTS,
            "maxTotalBytes": DEFAULT_MAX_TOTAL_BYTES,
            "batchDocuments": DEFAULT_BATCH_DOCUMENTS,
            "batchBytes": DEFAULT_BATCH_BYTES,
        },
        "currentAdapter": current_adapter_capability(),
        "apply": {
            "available": False,
            "reason": APPLY_UNSUPPORTED_REASON,
        },
        "secretsPrinted": False,
        "rawCorpusOmitted": True,
    }


def _run(args: argparse.Namespace) -> dict[str, object]:
    for name in (
        args.authoritative_database_url_env,
        args.auxiliary_database_url_env,
    ):
        if not SAFE_ENV_PATTERN.fullmatch(str(name)):
            raise SafetyError(
                "database URL environment names must start with TC_PHASE_F_"
            )
    target = TargetDeclaration(
        name=args.target_name,
        target_class=args.target_class,
        authoritative_database_name=args.authoritative_database_name,
        auxiliary_database_name=args.auxiliary_database_name,
        active_workspace=args.active_workspace,
    )
    preview = _preview(target)
    if not args.inspect:
        if args.confirm:
            raise SafetyError("--confirm is inspection-only")
        return preview
    if args.confirm != preview["confirmationRequired"]:
        raise SafetyError("--inspect requires the exact preview confirmation")
    if current_adapter_capability()["installedVersion"] != LIGHTRAG_VERSION:
        raise SafetyError(
            f"lightrag-hku=={LIGHTRAG_VERSION} is required for inspection"
        )
    source_location = parse_database_location(
        _required_environment(args.authoritative_database_url_env),
        expected_name=target.authoritative_database_name,
        target_class=target.target_class,
    )
    auxiliary_location = parse_database_location(
        _required_environment(args.auxiliary_database_url_env),
        expected_name=target.auxiliary_database_name,
        target_class=target.target_class,
    )
    if source_location.identity == auxiliary_location.identity:
        raise SafetyError("authoritative and auxiliary endpoints must be distinct")
    source_engine = create_engine(source_location.sqlalchemy_url, pool_pre_ping=True)
    auxiliary_engine = create_engine(
        auxiliary_location.sqlalchemy_url, pool_pre_ping=True
    )
    try:
        snapshot = PostgresSnapshotReader(source_engine, target=target).snapshot()
        auxiliary = inspect_auxiliary(
            auxiliary_engine,
            target=target,
            expected_snapshot=snapshot,
        )
    finally:
        source_engine.dispose()
        auxiliary_engine.dispose()
    return {
        **preview,
        "mode": "executed-read-only",
        "executes": True,
        "executionStatus": "INSPECTED_NOT_APPLIED",
        "snapshot": snapshot.public_dict(),
        "auxiliaryInspection": auxiliary,
        "apply": {
            "available": False,
            "executionStatus": "NOT_RUN",
            "reason": APPLY_UNSUPPORTED_REASON,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
    except (SafetyError, VerificationError, LiveApplyUnsupported) as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error), "rawCorpusOmitted": True},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # pragma: no cover - provider/database fault boundary
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"inspection failed: {error.__class__.__name__}",
                    "rawCorpusOmitted": True,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
