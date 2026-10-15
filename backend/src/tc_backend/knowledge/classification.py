"""Deterministic lifecycle classification for validated knowledge candidates."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from tc_backend.knowledge.models import (
    CandidateRelationKind,
    ClassifiedKnowledgeCandidate,
    KnowledgeNodeType,
    KnowledgeStatus,
    SlackSourceBundle,
    SlackSourceRevision,
    SourceRevisionOperation,
    ValidatedKnowledgeCandidate,
)


class KnowledgeStateClassifier:
    """Apply deletion and explicit provider relations with fixed precedence.

    Precedence is RETRACTED, SUPERSEDED, AMBIGUOUS, ACTIVE.  A conflict is
    ambiguous only while both endpoints would otherwise be current; this keeps
    a resolved reversal from remaining ambiguous because of its historical
    disagreement.
    """

    def classify(
        self,
        bundle: SlackSourceBundle,
        candidates: tuple[ValidatedKnowledgeCandidate, ...],
    ) -> tuple[ClassifiedKnowledgeCandidate, ...]:
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        deleted_message_ids = _deleted_message_ids(bundle)

        retracted_by: dict[str, set[str]] = {candidate_id: set() for candidate_id in by_id}
        superseded_by: dict[str, set[str]] = {candidate_id: set() for candidate_id in by_id}
        conflict_graph: dict[str, set[str]] = {candidate_id: set() for candidate_id in by_id}
        source_deleted: set[str] = set()
        explicit_self_retracted: set[str] = set()

        for candidate in candidates:
            if candidate.explicitly_retracted:
                explicit_self_retracted.add(candidate.candidate_id)
            if any(item.message_id in deleted_message_ids for item in candidate.evidence):
                source_deleted.add(candidate.candidate_id)
            for relation in candidate.relations:
                if relation.kind is CandidateRelationKind.RETRACTS:
                    retracted_by[relation.target_candidate_id].add(candidate.candidate_id)
                elif relation.kind is CandidateRelationKind.SUPERSEDES:
                    superseded_by[relation.target_candidate_id].add(candidate.candidate_id)
                elif relation.kind is CandidateRelationKind.CONFLICTS_WITH:
                    conflict_graph[candidate.candidate_id].add(relation.target_candidate_id)
                    conflict_graph[relation.target_candidate_id].add(candidate.candidate_id)

        # Fail closed when the provider emits different assertions for the same
        # atomic slot but omits an explicit conflict edge. Identical claims may
        # coexist as separately evidenced projections without becoming a
        # fabricated disagreement.
        by_topic: dict[tuple[KnowledgeNodeType, str], list[ValidatedKnowledgeCandidate]] = {}
        for candidate in candidates:
            by_topic.setdefault((candidate.node_type, candidate.topic_key), []).append(candidate)
        for topic_candidates in by_topic.values():
            for index, candidate in enumerate(topic_candidates):
                for other in topic_candidates[index + 1 :]:
                    if candidate.content.casefold() == other.content.casefold():
                        continue
                    conflict_graph[candidate.candidate_id].add(other.candidate_id)
                    conflict_graph[other.candidate_id].add(candidate.candidate_id)

        inactive_before_conflicts = source_deleted | explicit_self_retracted | {
            candidate_id for candidate_id, sources in retracted_by.items() if sources
        } | {candidate_id for candidate_id, sources in superseded_by.items() if sources}
        unresolved_conflicts = {
            candidate_id: {
                other
                for other in others
                if candidate_id not in inactive_before_conflicts
                and other not in inactive_before_conflicts
            }
            for candidate_id, others in conflict_graph.items()
        }

        classified: list[ClassifiedKnowledgeCandidate] = []
        for candidate in candidates:
            candidate_id = candidate.candidate_id
            retractors = tuple(sorted(retracted_by[candidate_id]))
            superseders = tuple(sorted(superseded_by[candidate_id]))
            conflicts = tuple(sorted(unresolved_conflicts[candidate_id]))
            if candidate_id in source_deleted:
                status = KnowledgeStatus.RETRACTED
                reason = "source_message_deleted"
            elif candidate_id in explicit_self_retracted:
                status = KnowledgeStatus.RETRACTED
                reason = "provider_marked_retracted"
            elif retractors:
                status = KnowledgeStatus.RETRACTED
                reason = "explicitly_retracted_by:" + ",".join(retractors)
            elif superseders:
                status = KnowledgeStatus.SUPERSEDED
                reason = "superseded_by:" + ",".join(superseders)
            elif conflicts:
                status = KnowledgeStatus.AMBIGUOUS
                reason = "unresolved_conflict_with:" + ",".join(conflicts)
            else:
                status = KnowledgeStatus.ACTIVE
                reason = "current_assertion"
            classified.append(
                ClassifiedKnowledgeCandidate(
                    candidate=candidate,
                    status=status,
                    reason=reason,
                    superseded_by_candidate_ids=superseders,
                    conflicting_candidate_ids=conflicts,
                    retracted_by_candidate_ids=retractors,
                )
            )
        return tuple(classified)


def _deleted_message_ids(bundle: SlackSourceBundle) -> frozenset[str]:
    latest_by_message: dict[str, SlackSourceRevision] = {}
    latest_key_by_message: dict[str, tuple[Decimal, int, str]] = {}
    operation_order = {
        SourceRevisionOperation.CREATE: 0,
        SourceRevisionOperation.EDIT: 1,
        SourceRevisionOperation.DELETE: 2,
    }
    for revision in bundle.revisions:
        try:
            timestamp = Decimal(revision.revision_ts)
        except InvalidOperation as exc:
            # The validator owns input rejection; direct classifier callers get
            # the same ordinary ValueError family instead of nondeterminism.
            raise ValueError(
                f"invalid Slack revision timestamp: {revision.revision_ts}"
            ) from exc
        sort_key = (
            timestamp,
            operation_order[revision.operation],
            revision.source_revision_id,
        )
        if sort_key > latest_key_by_message.get(
            revision.message_id, (Decimal("-1"), -1, "")
        ):
            latest_key_by_message[revision.message_id] = sort_key
            latest_by_message[revision.message_id] = revision
    return frozenset(
        message_id
        for message_id, revision in latest_by_message.items()
        if revision.operation is SourceRevisionOperation.DELETE
    )
