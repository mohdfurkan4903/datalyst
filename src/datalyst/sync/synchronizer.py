from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from datalyst.core.interfaces import CatalogIntrospector
from datalyst.core.models import SyncSummary, TableRef
from datalyst.core.schema_diff import FingerprintDiff, chunked, diff_fingerprints
from datalyst.metastore.repositories import CatalogRepository, SourceRepository


@dataclass(frozen=True)
class CheckResult:
    diff: FingerprintDiff
    fingerprints: dict[TableRef, str]
    removed: list[str]

    def batches(self, size: int) -> list[list[TableRef]]:
        return chunked(self.diff.to_sync, size)


class SourceSynchronizer:
    def __init__(
        self,
        catalog: CatalogRepository,
        sources: SourceRepository,
        source_id: int,
        introspector: CatalogIntrospector,
        batch_size: int = 200,
    ) -> None:
        self._catalog = catalog
        self._sources = sources
        self._source_id = source_id
        self._introspector = introspector
        self._batch_size = batch_size

    @property
    def batch_size(self) -> int:
        return self._batch_size

    def check(self) -> CheckResult:
        current = self._introspector.fingerprints()
        diff = diff_fingerprints(current, self._catalog.fingerprints(self._source_id))
        removed = self._catalog.remove_tables(self._source_id, diff.removed)
        self._catalog.update_row_estimates(
            self._source_id, {f.table: f.row_estimate for f in current}
        )
        self._sources.mark_checked(self._source_id)
        return CheckResult(
            diff=diff,
            fingerprints={f.table: f.fingerprint for f in current},
            removed=removed,
        )

    def sync_tables(
        self,
        refs: Sequence[TableRef],
        fingerprints: Mapping[TableRef, str] | None = None,
    ) -> SyncSummary:
        if fingerprints is None:
            wanted = set(refs)
            fingerprints = {
                f.table: f.fingerprint
                for f in self._introspector.fingerprints()
                if f.table in wanted
            }
        tables = self._introspector.describe_tables(refs)
        summary = self._catalog.apply_tables(self._source_id, tables, fingerprints)
        found = {table.ref for table in tables}
        missing = [ref for ref in refs if ref not in found]
        summary.removed.extend(self._catalog.remove_tables(self._source_id, missing))
        return summary

    def sync_now(self) -> SyncSummary:
        result = self.check()
        summary = SyncSummary(removed=result.removed)
        for batch in result.batches(self._batch_size):
            summary = summary.merge(self.sync_tables(batch, result.fingerprints))
        summary.tables = len(result.fingerprints)
        return summary
