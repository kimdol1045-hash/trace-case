from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from tc_backend.knowledge.lightrag import (
    KnowledgeFallback,
    LightRAGCallResult,
    LightRAGCallStatus,
    LightRAGIndexDocument,
    LightRAGIndexReceipt,
    LightRAGSearchCandidate,
    LightRAGUnavailableError,
)
from tc_backend.knowledge.lightrag_runtime import (
    LightRAGGenerationRuntime,
    LightRAGRuntimeResolver,
)
from tc_backend.repositories.lightrag_generation_repository import (
    ActiveGenerationBinding,
    LightRAGControlPlaneError,
)


def _binding(
    generation_id: str = "tc_generation_a",
    *,
    pointer_version: int = 1,
    workspace: str = "tc_workspace_a",
    seed: str = "a",
) -> ActiveGenerationBinding:
    digest = "sha256:" + seed * 64
    return ActiveGenerationBinding(
        generation_id=generation_id,
        pointer_version=pointer_version,
        pointer_updated_at=datetime(2026, 9, 2, tzinfo=UTC),
        authority_id="authority-test",
        provider_id="official-lightrag",
        provider_contract_version="tc-lightrag-generation-provider/v1",
        provider_distribution="lightrag-hku",
        provider_version="1.5.6",
        workspace=workspace,
        auxiliary_target_digest="sha256:" + "0" * 64,
        runtime_config_digest="sha256:" + "1" * 64,
        manifest_digest=digest,
        corpus_digest=digest,
        source_ledger_digest=digest,
        projection_digest=digest,
        pgvector_digest=digest,
        document_count=3,
        registration_digest=digest,
    )


class SharedBindingSource:
    def __init__(self, binding: ActiveGenerationBinding | None) -> None:
        self.binding = binding
        self.missing = False
        self.reads = 0

    def active_generation(self) -> ActiveGenerationBinding:
        self.reads += 1
        if self.missing:
            raise LightRAGControlPlaneError("ACTIVE_GENERATION_LEDGER_CORRUPT")
        if self.binding is None:
            raise LightRAGControlPlaneError("NO_ACTIVE_GENERATION")
        return self.binding


class FakeIndex:
    def __init__(
        self,
        generation_id: str,
        *,
        on_search: Callable[[], None] | None = None,
        on_index: Callable[[], None] | None = None,
    ) -> None:
        self.generation_id = generation_id
        self.on_search = on_search
        self.on_index = on_index
        self.searches = 0
        self.indexes = 0
        self.finalized = 0

    async def search(
        self, query: str, *, mode: str = "mix"
    ) -> LightRAGCallResult[LightRAGSearchCandidate]:
        self.searches += 1
        if self.on_search is not None:
            self.on_search()
        return LightRAGCallResult(
            status=LightRAGCallStatus.OK,
            value=LightRAGSearchCandidate(
                context=f"{self.generation_id}:{mode}:{query}",
                postgres_record_ids=(self.generation_id,),
            ),
        )

    async def index_document(
        self, document: LightRAGIndexDocument
    ) -> LightRAGCallResult[LightRAGIndexReceipt]:
        self.indexes += 1
        if self.on_index is not None:
            self.on_index()
        return LightRAGCallResult(
            status=LightRAGCallStatus.OK,
            value=LightRAGIndexReceipt(
                postgres_record_id=document.postgres_record_id,
                track_id=f"{self.generation_id}-track",
            ),
        )

    async def finalize(self) -> LightRAGCallResult[None]:
        self.finalized += 1
        return LightRAGCallResult(status=LightRAGCallStatus.OK)


class RecordingFactory:
    def __init__(
        self,
        custom: dict[tuple[int, str, str], FakeIndex] | None = None,
    ) -> None:
        self.custom = custom or {}
        self.bindings: list[ActiveGenerationBinding] = []
        self.indexes: list[FakeIndex] = []

    def __call__(self, binding: ActiveGenerationBinding) -> LightRAGGenerationRuntime:
        self.bindings.append(binding)
        index = self.custom.get(binding.cache_key) or FakeIndex(binding.generation_id)
        self.indexes.append(index)
        return LightRAGGenerationRuntime(binding=binding, index=index)


def _assert_unavailable(result: LightRAGCallResult[object]) -> None:
    assert result.status is LightRAGCallStatus.UNAVAILABLE
    assert isinstance(result.failure, LightRAGUnavailableError)
    assert result.failure.fallback is KnowledgeFallback.TYPED_GRAPH_PGVECTOR
    assert result.value is None


def test_no_active_missing_and_invalid_generation_fail_to_typed_fallback() -> None:
    for source in (
        SharedBindingSource(None),
        SharedBindingSource(_binding()),
        SharedBindingSource(replace(_binding(), pointer_version=0)),
    ):
        if source.binding is not None and source.binding.pointer_version == 1:
            source.missing = True
        factory = RecordingFactory()
        resolver = LightRAGRuntimeResolver(source, factory)

        result = asyncio.run(resolver.search("safe fallback"))

        _assert_unavailable(result)
        assert factory.bindings == []


def test_cache_is_versioned_and_rollback_to_same_identity_rebuilds_runtime() -> None:
    first = _binding()
    second = _binding("tc_generation_b", pointer_version=2, workspace="tc_workspace_b", seed="b")
    rolled_back = replace(first, pointer_version=3)
    source = SharedBindingSource(first)
    factory = RecordingFactory()
    resolver = LightRAGRuntimeResolver(source, factory)

    first_result = asyncio.run(resolver.search("one"))
    replay = asyncio.run(resolver.search("two"))
    source.binding = second
    second_result = asyncio.run(resolver.search("three"))
    source.binding = rolled_back
    rollback_result = asyncio.run(resolver.search("four"))

    assert first_result.require_value().context.startswith("tc_generation_a")
    assert replay.require_value().context.startswith("tc_generation_a")
    assert second_result.require_value().context.startswith("tc_generation_b")
    assert rollback_result.require_value().context.startswith("tc_generation_a")
    assert [binding.pointer_version for binding in factory.bindings] == [1, 2, 3]
    assert factory.indexes[0].searches == 2
    assert factory.indexes[0].finalized == 1
    assert factory.indexes[1].finalized == 1


def test_multiple_process_resolvers_observe_one_shared_durable_pointer() -> None:
    first = _binding()
    second = _binding("tc_generation_b", pointer_version=2, workspace="tc_workspace_b", seed="b")
    source = SharedBindingSource(first)
    factory_a = RecordingFactory()
    factory_b = RecordingFactory()
    process_a = LightRAGRuntimeResolver(source, factory_a)
    process_b = LightRAGRuntimeResolver(source, factory_b)

    assert asyncio.run(process_a.search("a")).require_value().postgres_record_ids == (
        "tc_generation_a",
    )
    assert asyncio.run(process_b.search("b")).require_value().postgres_record_ids == (
        "tc_generation_a",
    )
    source.binding = second
    assert asyncio.run(process_b.search("b2")).require_value().postgres_record_ids == (
        "tc_generation_b",
    )
    assert asyncio.run(process_a.search("a2")).require_value().postgres_record_ids == (
        "tc_generation_b",
    )

    assert [item.pointer_version for item in factory_a.bindings] == [1, 2]
    assert [item.pointer_version for item in factory_b.bindings] == [1, 2]


def test_pointer_change_during_query_discards_the_complete_stale_candidate() -> None:
    first = _binding()
    second = _binding("tc_generation_b", pointer_version=2, workspace="tc_workspace_b", seed="b")
    source = SharedBindingSource(first)
    stale = FakeIndex(first.generation_id, on_search=lambda: setattr(source, "binding", second))
    factory = RecordingFactory({first.cache_key: stale})
    resolver = LightRAGRuntimeResolver(source, factory)

    result = asyncio.run(resolver.search("must not leak"))

    _assert_unavailable(result)
    assert stale.searches == 1
    assert stale.finalized == 1
    fresh = asyncio.run(resolver.search("fresh"))
    assert fresh.require_value().postgres_record_ids == (second.generation_id,)


def test_in_flight_stale_cache_entry_is_retired_after_its_last_lease() -> None:
    async def scenario() -> None:
        first = _binding()
        second = _binding(
            "tc_generation_b", pointer_version=2, workspace="tc_workspace_b", seed="b"
        )
        source = SharedBindingSource(first)
        entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingIndex(FakeIndex):
            async def search(
                self, query: str, *, mode: str = "mix"
            ) -> LightRAGCallResult[LightRAGSearchCandidate]:
                self.searches += 1
                entered.set()
                await release.wait()
                return LightRAGCallResult(
                    status=LightRAGCallStatus.OK,
                    value=LightRAGSearchCandidate(
                        context=f"{self.generation_id}:{mode}:{query}",
                        postgres_record_ids=(self.generation_id,),
                    ),
                )

        stale_index = BlockingIndex(first.generation_id)
        resolver = LightRAGRuntimeResolver(source, RecordingFactory({first.cache_key: stale_index}))
        stale_task = asyncio.create_task(resolver.search("stale"))
        await entered.wait()

        source.binding = second
        fresh = await resolver.search("fresh")
        assert fresh.require_value().postgres_record_ids == (second.generation_id,)
        assert stale_index.finalized == 0

        release.set()
        stale = await stale_task
        _assert_unavailable(stale)
        assert stale_index.finalized == 1

    asyncio.run(scenario())


def test_disappearing_generation_and_stale_index_receipt_are_not_exposed() -> None:
    binding = _binding()
    source = SharedBindingSource(binding)
    disappearing = FakeIndex(
        binding.generation_id, on_search=lambda: setattr(source, "missing", True)
    )
    resolver = LightRAGRuntimeResolver(source, RecordingFactory({binding.cache_key: disappearing}))

    _assert_unavailable(asyncio.run(resolver.search("disappears")))
    source.missing = False
    replacement = replace(binding, pointer_version=2)
    source.binding = replacement
    stale_write = FakeIndex(
        replacement.generation_id,
        on_index=lambda: setattr(source, "binding", replace(replacement, pointer_version=3)),
    )
    write_resolver = LightRAGRuntimeResolver(
        source, RecordingFactory({replacement.cache_key: stale_write})
    )
    result = asyncio.run(
        write_resolver.index_document(
            LightRAGIndexDocument(
                postgres_record_id="00000000-0000-0000-0000-000000000001",
                content="verified content",
                source_uri="postgresql://knowledge/nodes/1",
            )
        )
    )

    _assert_unavailable(result)
    assert stale_write.indexes == 1


def test_factory_must_attest_the_exact_target_binding() -> None:
    binding = _binding()
    source = SharedBindingSource(binding)

    def wrong_factory(_binding: ActiveGenerationBinding) -> LightRAGGenerationRuntime:
        wrong = replace(binding, workspace="tc_wrong_workspace")
        return LightRAGGenerationRuntime(binding=wrong, index=FakeIndex(wrong.generation_id))

    resolver = LightRAGRuntimeResolver(source, wrong_factory)

    _assert_unavailable(asyncio.run(resolver.search("wrong target")))
