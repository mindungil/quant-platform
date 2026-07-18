"""Immutable dataset registries and reproducible experiment manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from string import hexdigits

from .strategy_decision import DigestAlgorithm

ZERO = Decimal("0")


class ReproducibilityMode(StrEnum):
    EXACT_BYTES = "EXACT_BYTES"
    NUMERIC_TOLERANCE = "NUMERIC_TOLERANCE"


class ExperimentRevisionKind(StrEnum):
    CODE = "CODE"
    DEPENDENCY_LOCK = "DEPENDENCY_LOCK"
    CONFIG = "CONFIG"
    RULE = "RULE"
    ENVIRONMENT = "ENVIRONMENT"


class SeedPolicyKind(StrEnum):
    FIXED = "FIXED"
    DETERMINISTIC_NO_RNG = "DETERMINISTIC_NO_RNG"


@dataclass(frozen=True, slots=True)
class ContentDigest:
    algorithm: DigestAlgorithm
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.lower()
        expected_length = 40 if self.algorithm is DigestAlgorithm.GIT_SHA1 else 64
        if len(normalized) != expected_length or any(
            character not in hexdigits for character in normalized
        ):
            raise ValueError(
                f"digest must be a {expected_length}-character hexadecimal value for "
                f"{self.algorithm.value}"
            )
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class NumericTolerance:
    absolute: Decimal | None = None
    relative: Decimal | None = None
    nan_equal: bool = False

    def __post_init__(self) -> None:
        if self.absolute is None and self.relative is None:
            raise ValueError("numeric tolerance requires absolute or relative tolerance")
        for name in ("absolute", "relative"):
            value = getattr(self, name)
            if value is not None:
                if not value.is_finite() or value < ZERO:
                    raise ValueError(f"{name} tolerance must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ReproducibilityPolicy:
    mode: ReproducibilityMode
    tolerance: NumericTolerance | None = None

    def __post_init__(self) -> None:
        if self.mode is ReproducibilityMode.EXACT_BYTES and self.tolerance is not None:
            raise ValueError("EXACT_BYTES must not define a numeric tolerance")
        if self.mode is ReproducibilityMode.NUMERIC_TOLERANCE and self.tolerance is None:
            raise ValueError("NUMERIC_TOLERANCE requires a tolerance policy")


@dataclass(frozen=True, slots=True)
class SourceObject:
    source_id: str
    reference: str
    digest: ContentDigest
    observed_at: datetime
    media_type: str
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        for name in ("source_id", "reference", "media_type"):
            _require_text(getattr(self, name), name)
        _require_aware(self.observed_at, "observed_at")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class DatasetArtifact:
    artifact_id: str
    path: str
    media_type: str
    digest: ContentDigest
    size_bytes: int
    rows: int | None = None
    schema_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in ("artifact_id", "path", "media_type"):
            _require_text(getattr(self, name), name)
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if self.rows is not None and self.rows < 0:
            raise ValueError("rows must be non-negative")
        if self.schema_sha256 is not None:
            _require_sha256(self.schema_sha256, "schema_sha256")
            object.__setattr__(self, "schema_sha256", self.schema_sha256.lower())


@dataclass(frozen=True, slots=True)
class TransformStep:
    transform_id: str
    name: str
    version: str
    implementation_reference: str
    implementation_digest: ContentDigest
    parameters_sha256: str
    input_refs: tuple[str, ...]
    output_artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "transform_id",
            "name",
            "version",
            "implementation_reference",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_sha256(self.parameters_sha256, "parameters_sha256")
        object.__setattr__(self, "parameters_sha256", self.parameters_sha256.lower())
        _require_unique_text(self.input_refs, "input_refs")
        _require_unique_text(self.output_artifact_ids, "output_artifact_ids")


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    dataset_id: str
    schema_version: str
    created_at: datetime
    source_objects: tuple[SourceObject, ...]
    transforms: tuple[TransformStep, ...]
    artifacts: tuple[DatasetArtifact, ...]
    reproducibility: ReproducibilityPolicy
    parent_dataset_ids: tuple[str, ...] = ()
    time_start: datetime | None = None
    time_end_exclusive: datetime | None = None
    quality_report_artifact_id: str | None = None
    legacy_manifest_digest: ContentDigest | None = None
    legacy_schema: str | None = None

    def __post_init__(self) -> None:
        for name in ("dataset_id", "schema_version"):
            _require_text(getattr(self, name), name)
        _require_aware(self.created_at, "created_at")
        _require_unique_text(self.parent_dataset_ids, "parent_dataset_ids", allow_empty=True)
        if self.dataset_id in self.parent_dataset_ids:
            raise ValueError("a dataset must not be its own parent")
        _validate_optional_time_range(self.time_start, self.time_end_exclusive)
        if not self.source_objects and not self.parent_dataset_ids:
            raise ValueError("a dataset requires source objects or parent datasets")
        if not self.artifacts:
            raise ValueError("artifacts must not be empty")
        _require_unique_text(
            tuple(source.source_id for source in self.source_objects),
            "source object IDs",
            allow_empty=True,
        )
        _require_unique_text(
            tuple(transform.transform_id for transform in self.transforms),
            "transform IDs",
            allow_empty=True,
        )
        _require_unique_text(
            tuple(artifact.artifact_id for artifact in self.artifacts),
            "artifact IDs",
        )
        if self.legacy_schema is not None:
            _require_text(self.legacy_schema, "legacy_schema")
        if (self.legacy_manifest_digest is None) != (self.legacy_schema is None):
            raise ValueError(
                "legacy_manifest_digest and legacy_schema must be provided together"
            )
        self._validate_transform_references()
        if self.quality_report_artifact_id is not None:
            artifact_ids = {artifact.artifact_id for artifact in self.artifacts}
            if self.quality_report_artifact_id not in artifact_ids:
                raise ValueError("quality_report_artifact_id references a missing artifact")

    def _validate_transform_references(self) -> None:
        source_refs = {source.source_id for source in self.source_objects}
        artifact_refs = {artifact.artifact_id for artifact in self.artifacts}
        parent_refs = {f"dataset:{dataset_id}" for dataset_id in self.parent_dataset_ids}
        valid_inputs = source_refs | artifact_refs | parent_refs
        produced_artifacts: set[str] = set()
        for transform in self.transforms:
            missing_inputs = set(transform.input_refs) - valid_inputs
            if missing_inputs:
                raise ValueError(
                    f"transform references missing inputs: {sorted(missing_inputs)}"
                )
            missing_outputs = set(transform.output_artifact_ids) - artifact_refs
            if missing_outputs:
                raise ValueError(
                    f"transform references missing output artifacts: {sorted(missing_outputs)}"
                )
            overlap = produced_artifacts & set(transform.output_artifact_ids)
            if overlap:
                raise ValueError(
                    f"artifacts must be produced by at most one transform: {sorted(overlap)}"
                )
            produced_artifacts.update(transform.output_artifact_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
            "time_start": _optional_datetime(self.time_start),
            "time_end_exclusive": _optional_datetime(self.time_end_exclusive),
            "parent_dataset_ids": list(self.parent_dataset_ids),
            "source_objects": [
                {
                    "source_id": source.source_id,
                    "reference": source.reference,
                    "digest": _digest_to_dict(source.digest),
                    "observed_at": source.observed_at.isoformat(),
                    "media_type": source.media_type,
                    "size_bytes": source.size_bytes,
                }
                for source in self.source_objects
            ],
            "transforms": [
                {
                    "transform_id": transform.transform_id,
                    "name": transform.name,
                    "version": transform.version,
                    "implementation_reference": transform.implementation_reference,
                    "implementation_digest": _digest_to_dict(
                        transform.implementation_digest
                    ),
                    "parameters_sha256": transform.parameters_sha256,
                    "input_refs": list(transform.input_refs),
                    "output_artifact_ids": list(transform.output_artifact_ids),
                }
                for transform in self.transforms
            ],
            "artifacts": [
                {
                    "artifact_id": artifact.artifact_id,
                    "path": artifact.path,
                    "media_type": artifact.media_type,
                    "digest": _digest_to_dict(artifact.digest),
                    "size_bytes": artifact.size_bytes,
                    "rows": artifact.rows,
                    "schema_sha256": artifact.schema_sha256,
                }
                for artifact in self.artifacts
            ],
            "quality_report_artifact_id": self.quality_report_artifact_id,
            "reproducibility": _policy_to_dict(self.reproducibility),
            "legacy_manifest_digest": (
                None
                if self.legacy_manifest_digest is None
                else _digest_to_dict(self.legacy_manifest_digest)
            ),
            "legacy_schema": self.legacy_schema,
        }

    def to_json(self) -> str:
        return _deterministic_json(self.to_dict())

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetLineage:
    dataset_id: str
    ancestors: tuple[str, ...]
    descendants: tuple[str, ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatasetRegistry:
    manifests: tuple[DatasetManifest, ...] = ()

    def __post_init__(self) -> None:
        _require_unique_text(
            tuple(manifest.dataset_id for manifest in self.manifests),
            "dataset IDs",
            allow_empty=True,
        )
        self._validate_parent_references()
        self._validate_acyclic()

    def _validate_parent_references(self) -> None:
        dataset_ids = {manifest.dataset_id for manifest in self.manifests}
        for manifest in self.manifests:
            missing = set(manifest.parent_dataset_ids) - dataset_ids
            if missing:
                raise ValueError(
                    f"dataset references missing parent datasets: {sorted(missing)}"
                )

    def _validate_acyclic(self) -> None:
        by_id = {manifest.dataset_id: manifest for manifest in self.manifests}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(dataset_id: str) -> None:
            if dataset_id in visiting:
                raise ValueError("dataset lineage must not contain a cycle")
            if dataset_id in visited:
                return
            visiting.add(dataset_id)
            for parent in by_id[dataset_id].parent_dataset_ids:
                visit(parent)
            visiting.remove(dataset_id)
            visited.add(dataset_id)

        for dataset_id in sorted(by_id):
            visit(dataset_id)

    def register(self, manifest: DatasetManifest) -> DatasetRegistry:
        existing = next(
            (
                candidate
                for candidate in self.manifests
                if candidate.dataset_id == manifest.dataset_id
            ),
            None,
        )
        if existing is not None:
            if existing.content_sha256() == manifest.content_sha256():
                return self
            raise ValueError("dataset ID is already registered with different content")
        return DatasetRegistry(self.manifests + (manifest,))

    def get(self, dataset_id: str) -> DatasetManifest:
        _require_text(dataset_id, "dataset_id")
        for manifest in self.manifests:
            if manifest.dataset_id == dataset_id:
                return manifest
        raise KeyError(dataset_id)

    def ancestors(self, dataset_id: str) -> tuple[str, ...]:
        self.get(dataset_id)
        by_id = {manifest.dataset_id: manifest for manifest in self.manifests}
        result: list[str] = []
        seen: set[str] = set()

        def collect(current: str) -> None:
            for parent in sorted(by_id[current].parent_dataset_ids):
                collect(parent)
                if parent not in seen:
                    seen.add(parent)
                    result.append(parent)

        collect(dataset_id)
        return tuple(result)

    def descendants(self, dataset_id: str) -> tuple[str, ...]:
        self.get(dataset_id)
        children: dict[str, list[str]] = {manifest.dataset_id: [] for manifest in self.manifests}
        for manifest in self.manifests:
            for parent in manifest.parent_dataset_ids:
                children[parent].append(manifest.dataset_id)
        result: list[str] = []
        seen: set[str] = set()

        def collect(current: str) -> None:
            for child in sorted(children[current]):
                if child not in seen:
                    seen.add(child)
                    result.append(child)
                    collect(child)

        collect(dataset_id)
        return tuple(result)

    def lineage(self, dataset_id: str) -> DatasetLineage:
        manifest = self.get(dataset_id)
        source_ids = tuple(sorted(source.source_id for source in manifest.source_objects))
        return DatasetLineage(
            dataset_id=dataset_id,
            ancestors=self.ancestors(dataset_id),
            descendants=self.descendants(dataset_id),
            source_ids=source_ids,
        )

    def find_by_source_digest(self, digest: ContentDigest) -> tuple[str, ...]:
        return tuple(
            manifest.dataset_id
            for manifest in self.manifests
            if any(source.digest == digest for source in manifest.source_objects)
        )

    def to_json(self) -> str:
        payload = {
            "datasets": [
                manifest.to_dict()
                for manifest in sorted(self.manifests, key=lambda item: item.dataset_id)
            ]
        }
        return _deterministic_json(payload)

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SeedPolicy:
    kind: SeedPolicyKind
    seed: int | None = None

    def __post_init__(self) -> None:
        if self.kind is SeedPolicyKind.FIXED:
            if self.seed is None:
                raise ValueError("FIXED seed policy requires a seed")
            if self.seed < 0 or self.seed > 2**63 - 1:
                raise ValueError("seed must be between 0 and 2^63-1")
        elif self.seed is not None:
            raise ValueError("DETERMINISTIC_NO_RNG must not define a seed")


@dataclass(frozen=True, slots=True)
class ExperimentRevision:
    revision_id: str
    kind: ExperimentRevisionKind
    reference: str
    version: str
    digest: ContentDigest

    def __post_init__(self) -> None:
        for name in ("revision_id", "reference", "version"):
            _require_text(getattr(self, name), name)
        if self.digest.algorithm is DigestAlgorithm.GIT_SHA1:
            if self.version.lower() != self.digest.value:
                raise ValueError("GIT_SHA1 revision version must equal its digest")


@dataclass(frozen=True, slots=True)
class ExperimentOutput:
    output_id: str
    reference: str
    digest: ContentDigest
    reproducibility: ReproducibilityPolicy

    def __post_init__(self) -> None:
        for name in ("output_id", "reference"):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    experiment_id: str
    schema_version: str
    strategy_id: str
    created_at: datetime
    dataset_ids: tuple[str, ...]
    revisions: tuple[ExperimentRevision, ...]
    seed_policy: SeedPolicy
    outputs: tuple[ExperimentOutput, ...]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("experiment_id", "schema_version", "strategy_id"):
            _require_text(getattr(self, name), name)
        _require_aware(self.created_at, "created_at")
        _require_unique_text(self.dataset_ids, "dataset_ids")
        _require_unique_text(
            tuple(revision.revision_id for revision in self.revisions),
            "revision IDs",
        )
        _require_unique_text(
            tuple(output.output_id for output in self.outputs),
            "output IDs",
        )
        _require_unique_text(self.notes, "notes", allow_empty=True)
        kinds = {revision.kind for revision in self.revisions}
        required = {
            ExperimentRevisionKind.CODE,
            ExperimentRevisionKind.DEPENDENCY_LOCK,
            ExperimentRevisionKind.RULE,
            ExperimentRevisionKind.ENVIRONMENT,
        }
        missing = required - kinds
        if missing:
            labels = ", ".join(sorted(kind.value for kind in missing))
            raise ValueError(f"experiment revisions are missing required kinds: {labels}")

    def validate_registry(self, registry: DatasetRegistry) -> None:
        missing: list[str] = []
        for dataset_id in self.dataset_ids:
            try:
                registry.get(dataset_id)
            except KeyError:
                missing.append(dataset_id)
        if missing:
            raise ValueError(
                f"experiment references unregistered datasets: {sorted(missing)}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "created_at": self.created_at.isoformat(),
            "dataset_ids": list(self.dataset_ids),
            "revisions": [
                {
                    "revision_id": revision.revision_id,
                    "kind": revision.kind.value,
                    "reference": revision.reference,
                    "version": revision.version,
                    "digest": _digest_to_dict(revision.digest),
                }
                for revision in self.revisions
            ],
            "seed_policy": {
                "kind": self.seed_policy.kind.value,
                "seed": self.seed_policy.seed,
            },
            "outputs": [
                {
                    "output_id": output.output_id,
                    "reference": output.reference,
                    "digest": _digest_to_dict(output.digest),
                    "reproducibility": _policy_to_dict(output.reproducibility),
                }
                for output in self.outputs
            ],
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        return _deterministic_json(self.to_dict())

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


def _digest_to_dict(digest: ContentDigest) -> dict[str, str]:
    return {"algorithm": digest.algorithm.value, "value": digest.value}


def _policy_to_dict(policy: ReproducibilityPolicy) -> dict[str, object]:
    tolerance: dict[str, object] | None = None
    if policy.tolerance is not None:
        tolerance = {
            "absolute": (
                None
                if policy.tolerance.absolute is None
                else str(policy.tolerance.absolute)
            ),
            "relative": (
                None
                if policy.tolerance.relative is None
                else str(policy.tolerance.relative)
            ),
            "nan_equal": policy.tolerance.nan_equal,
        }
    return {"mode": policy.mode.value, "tolerance": tolerance}


def _deterministic_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _optional_datetime(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _validate_optional_time_range(
    start: datetime | None,
    end_exclusive: datetime | None,
) -> None:
    if (start is None) != (end_exclusive is None):
        raise ValueError("time_start and time_end_exclusive must be provided together")
    if start is not None and end_exclusive is not None:
        _require_aware(start, "time_start")
        _require_aware(end_exclusive, "time_end_exclusive")
        if end_exclusive <= start:
            raise ValueError("time_end_exclusive must follow time_start")


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in hexdigits for character in normalized):
        raise ValueError(f"{name} must be a 64-character hexadecimal SHA-256 digest")


def _require_unique_text(
    values: tuple[str, ...],
    name: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not allow_empty and not values:
        raise ValueError(f"{name} must not be empty")
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must contain non-empty values")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")
