from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform.experiment import (
    ContentDigest,
    DatasetArtifact,
    DatasetManifest,
    DatasetRegistry,
    ExperimentManifest,
    ExperimentOutput,
    ExperimentRevision,
    ExperimentRevisionKind,
    NumericTolerance,
    ReproducibilityMode,
    ReproducibilityPolicy,
    SeedPolicy,
    SeedPolicyKind,
    SourceObject,
    TransformStep,
)
from quant_platform.strategy_decision import DigestAlgorithm

NOW = datetime(2026, 7, 18, tzinfo=UTC)
SHA256_A = "a" * 64
SHA256_B = "b" * 64
SHA256_C = "c" * 64
GIT_A = "1" * 40


def digest(value: str = SHA256_A) -> ContentDigest:
    return ContentDigest(DigestAlgorithm.SHA256, value)


def source(source_id: str = "source-1") -> SourceObject:
    return SourceObject(
        source_id=source_id,
        reference=f"https://example.test/{source_id}",
        digest=digest(),
        observed_at=NOW,
        media_type="application/zip",
        size_bytes=10,
    )


def artifact(artifact_id: str = "data") -> DatasetArtifact:
    return DatasetArtifact(
        artifact_id=artifact_id,
        path=f"generated/{artifact_id}.csv",
        media_type="text/csv",
        digest=digest(SHA256_B),
        size_bytes=100,
        rows=5,
        schema_sha256=SHA256_C,
    )


def dataset(
    dataset_id: str = "dataset-v1",
    *,
    parents: tuple[str, ...] = (),
    sources: tuple[SourceObject, ...] | None = None,
) -> DatasetManifest:
    source_objects = (source(),) if sources is None and not parents else sources or ()
    input_refs = (
        (source_objects[0].source_id,)
        if source_objects
        else tuple(f"dataset:{parent}" for parent in parents)
    )
    return DatasetManifest(
        dataset_id=dataset_id,
        schema_version="dataset-manifest-v1",
        created_at=NOW,
        source_objects=source_objects,
        transforms=(
            TransformStep(
                transform_id=f"transform-{dataset_id}",
                name="normalize",
                version="1",
                implementation_reference="scripts/normalize.py",
                implementation_digest=digest(SHA256_C),
                parameters_sha256=SHA256_A,
                input_refs=input_refs,
                output_artifact_ids=("data",),
            ),
        ),
        artifacts=(artifact(),),
        reproducibility=ReproducibilityPolicy(ReproducibilityMode.EXACT_BYTES),
        parent_dataset_ids=parents,
        time_start=NOW,
        time_end_exclusive=NOW + timedelta(days=1),
    )


def experiment(dataset_id: str = "dataset-v1") -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id="experiment-v1",
        schema_version="experiment-manifest-v1",
        strategy_id="momentum",
        created_at=NOW,
        dataset_ids=(dataset_id,),
        revisions=(
            ExperimentRevision(
                "code",
                ExperimentRevisionKind.CODE,
                "https://github.com/example/private",
                GIT_A,
                ContentDigest(DigestAlgorithm.GIT_SHA1, GIT_A),
            ),
            ExperimentRevision(
                "dependencies",
                ExperimentRevisionKind.DEPENDENCY_LOCK,
                "requirements.lock",
                "requirements-v1",
                digest(SHA256_A),
            ),
            ExperimentRevision(
                "rules",
                ExperimentRevisionKind.RULE,
                "validation/spec.json",
                "rules-v1",
                digest(SHA256_B),
            ),
            ExperimentRevision(
                "environment",
                ExperimentRevisionKind.ENVIRONMENT,
                "python-runtime.json",
                "cpython-3.11",
                digest(SHA256_C),
            ),
        ),
        seed_policy=SeedPolicy(SeedPolicyKind.DETERMINISTIC_NO_RNG),
        outputs=(
            ExperimentOutput(
                output_id="report",
                reference="validation/report.json",
                digest=digest(SHA256_A),
                reproducibility=ReproducibilityPolicy(
                    ReproducibilityMode.EXACT_BYTES
                ),
            ),
        ),
    )


def test_digest_and_reproducibility_policies_are_strict() -> None:
    with pytest.raises(ValueError, match="64-character"):
        ContentDigest(DigestAlgorithm.SHA256, "abc")
    with pytest.raises(ValueError, match="must not define"):
        ReproducibilityPolicy(
            ReproducibilityMode.EXACT_BYTES,
            NumericTolerance(absolute=Decimal("0.01")),
        )
    with pytest.raises(ValueError, match="requires a tolerance"):
        ReproducibilityPolicy(ReproducibilityMode.NUMERIC_TOLERANCE)

    policy = ReproducibilityPolicy(
        ReproducibilityMode.NUMERIC_TOLERANCE,
        NumericTolerance(relative=Decimal("0.000001"), nan_equal=True),
    )
    assert policy.tolerance is not None
    assert policy.tolerance.relative == Decimal("0.000001")


def test_dataset_manifest_validates_transform_references() -> None:
    valid = dataset()
    assert valid.content_sha256() == valid.content_sha256()

    with pytest.raises(ValueError, match="missing inputs"):
        DatasetManifest(
            dataset_id="bad",
            schema_version="v1",
            created_at=NOW,
            source_objects=(source(),),
            transforms=(
                TransformStep(
                    transform_id="bad-transform",
                    name="bad",
                    version="1",
                    implementation_reference="bad.py",
                    implementation_digest=digest(),
                    parameters_sha256=SHA256_A,
                    input_refs=("missing",),
                    output_artifact_ids=("data",),
                ),
            ),
            artifacts=(artifact(),),
            reproducibility=ReproducibilityPolicy(
                ReproducibilityMode.EXACT_BYTES
            ),
        )


def test_registry_resolves_lineage_and_source_digest() -> None:
    root = dataset("root")
    child = dataset("child", parents=("root",), sources=())
    grandchild = dataset("grandchild", parents=("child",), sources=())
    registry = DatasetRegistry().register(root).register(child).register(grandchild)

    assert registry.ancestors("grandchild") == ("root", "child")
    assert registry.descendants("root") == ("child", "grandchild")
    assert registry.lineage("child").ancestors == ("root",)
    assert registry.find_by_source_digest(digest()) == ("root",)
    assert registry.content_sha256() == registry.content_sha256()


def test_registry_rejects_missing_parent_and_cycle() -> None:
    with pytest.raises(ValueError, match="missing parent"):
        DatasetRegistry((dataset("child", parents=("missing",), sources=()),))

    a = dataset("a", parents=("b",), sources=())
    b = dataset("b", parents=("a",), sources=())
    with pytest.raises(ValueError, match="cycle"):
        DatasetRegistry((a, b))


def test_registry_registration_is_idempotent_but_conflicts_fail() -> None:
    original = dataset()
    registry = DatasetRegistry().register(original)
    assert registry.register(original) is registry

    conflicting = DatasetManifest(
        dataset_id=original.dataset_id,
        schema_version=original.schema_version,
        created_at=original.created_at,
        source_objects=original.source_objects,
        transforms=original.transforms,
        artifacts=(
            DatasetArtifact(
                artifact_id="data",
                path="generated/data.csv",
                media_type="text/csv",
                digest=digest(SHA256_C),
                size_bytes=100,
                rows=5,
                schema_sha256=SHA256_C,
            ),
        ),
        reproducibility=original.reproducibility,
        time_start=original.time_start,
        time_end_exclusive=original.time_end_exclusive,
    )
    with pytest.raises(ValueError, match="different content"):
        registry.register(conflicting)


def test_experiment_manifest_pins_required_revisions_and_registry() -> None:
    registry = DatasetRegistry().register(dataset())
    manifest = experiment()
    manifest.validate_registry(registry)

    assert manifest.to_json() == manifest.to_json()
    assert manifest.content_sha256() == manifest.content_sha256()
    assert '"kind": "DETERMINISTIC_NO_RNG"' in manifest.to_json()

    with pytest.raises(ValueError, match="unregistered"):
        experiment("missing").validate_registry(registry)


def test_experiment_rejects_missing_revision_kind_and_implicit_seed() -> None:
    valid = experiment()
    with pytest.raises(ValueError, match="ENVIRONMENT"):
        ExperimentManifest(
            experiment_id=valid.experiment_id,
            schema_version=valid.schema_version,
            strategy_id=valid.strategy_id,
            created_at=valid.created_at,
            dataset_ids=valid.dataset_ids,
            revisions=tuple(
                revision
                for revision in valid.revisions
                if revision.kind is not ExperimentRevisionKind.ENVIRONMENT
            ),
            seed_policy=valid.seed_policy,
            outputs=valid.outputs,
        )
    with pytest.raises(ValueError, match="requires a seed"):
        SeedPolicy(SeedPolicyKind.FIXED)
    with pytest.raises(ValueError, match="must not define"):
        SeedPolicy(SeedPolicyKind.DETERMINISTIC_NO_RNG, seed=0)


def test_legacy_manifest_metadata_is_all_or_nothing() -> None:
    with pytest.raises(ValueError, match="provided together"):
        DatasetManifest(
            dataset_id="legacy",
            schema_version="v1",
            created_at=NOW,
            source_objects=(source(),),
            transforms=(),
            artifacts=(artifact(),),
            reproducibility=ReproducibilityPolicy(
                ReproducibilityMode.EXACT_BYTES
            ),
            legacy_schema="legacy-momentum-v1",
        )
