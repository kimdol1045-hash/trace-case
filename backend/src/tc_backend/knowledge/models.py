"""Provider-neutral value objects for Slack knowledge extraction.

The PostgreSQL Slack and ontology ledgers remain authoritative.  Objects in
this module are immutable extraction/classification inputs and projections;
they are not a second system of record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class KnowledgeNodeType(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    DECISION = "DECISION"
    CONSTRAINT = "CONSTRAINT"
    OPEN_QUESTION = "OPEN_QUESTION"


class KnowledgeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    AMBIGUOUS = "AMBIGUOUS"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"


class SourceRevisionOperation(StrEnum):
    CREATE = "CREATE"
    EDIT = "EDIT"
    DELETE = "DELETE"


class CandidateRelationKind(StrEnum):
    SUPERSEDES = "SUPERSEDES"
    CONFLICTS_WITH = "CONFLICTS_WITH"
    RETRACTS = "RETRACTS"


@dataclass(frozen=True, slots=True)
class SlackSourceRevision:
    """One immutable revision in a root-message/thread source bundle.

    ``message_id`` identifies the logical ledger message, ``message_ts`` keeps
    Slack chronology, and ``source_revision_id`` identifies this stored ledger
    revision. Delete observations retain the logical identity and may have no
    body.
    """

    source_revision_id: str
    message_id: str
    revision_ts: str
    author_id: str | None
    body: str | None
    permalink: str | None
    operation: SourceRevisionOperation = SourceRevisionOperation.CREATE
    message_ts: str | None = None
    is_root: bool = False
    recorded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SlackSourceBundle:
    workspace_id: str
    channel_id: str
    thread_ts: str
    revisions: tuple[SlackSourceRevision, ...]
    work_item_id: str | None = None
    source_version: int = 0


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    """Provider-proposed excerpt anchored to one immutable source revision."""

    source_revision_id: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class CandidateRelation:
    kind: CandidateRelationKind | str
    target_candidate_id: str


@dataclass(frozen=True, slots=True)
class ProviderKnowledgeCandidate:
    """Untrusted semantic output produced by an extraction provider.

    Providers describe semantic relations explicitly.  The deterministic
    validator verifies every citation and reference before the classifier may
    use those relations.
    """

    candidate_id: str
    node_type: KnowledgeNodeType | str
    content: str
    # Atomic semantic slot. Different current claims with the same type/key
    # fail closed to AMBIGUOUS unless an explicit transition resolves them.
    topic_key: str
    confidence: float
    evidence: tuple[ProviderEvidence, ...]
    relations: tuple[CandidateRelation, ...] = ()
    explicitly_retracted: bool = False


@dataclass(frozen=True, slots=True)
class ProviderExtraction:
    provider: str
    model: str
    schema_version: str
    candidates: tuple[ProviderKnowledgeCandidate, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeEvidence:
    source_revision_id: str
    message_id: str
    revision_ts: str
    author_id: str | None
    permalink: str | None
    excerpt: str


@dataclass(frozen=True, slots=True)
class ValidatedCandidateRelation:
    kind: CandidateRelationKind
    target_candidate_id: str


@dataclass(frozen=True, slots=True)
class ValidatedKnowledgeCandidate:
    candidate_id: str
    node_type: KnowledgeNodeType
    content: str
    topic_key: str
    confidence: float
    evidence: tuple[KnowledgeEvidence, ...]
    relations: tuple[ValidatedCandidateRelation, ...]
    explicitly_retracted: bool


@dataclass(frozen=True, slots=True)
class ValidatedExtraction:
    provider: str
    model: str
    schema_version: str
    candidates: tuple[ValidatedKnowledgeCandidate, ...]


@dataclass(frozen=True, slots=True)
class ClassifiedKnowledgeCandidate:
    candidate: ValidatedKnowledgeCandidate
    status: KnowledgeStatus
    reason: str
    superseded_by_candidate_ids: tuple[str, ...] = ()
    conflicting_candidate_ids: tuple[str, ...] = ()
    retracted_by_candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeExtractionResult:
    provider: str
    model: str
    schema_version: str
    candidates: tuple[ClassifiedKnowledgeCandidate, ...]

    @property
    def active(self) -> tuple[ClassifiedKnowledgeCandidate, ...]:
        return tuple(item for item in self.candidates if item.status is KnowledgeStatus.ACTIVE)

    @property
    def excluded(self) -> tuple[ClassifiedKnowledgeCandidate, ...]:
        return tuple(item for item in self.candidates if item.status is not KnowledgeStatus.ACTIVE)
