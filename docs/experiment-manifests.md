# Dataset Registry and Experiment Manifest

The public experiment contracts make data lineage and experiment reproduction explicit before a strategy result is interpreted.

## Dataset Manifest

`DatasetManifest` records:

- a stable dataset ID and schema version,
- immutable upstream source references and content digests,
- ordered transform IDs, versions, implementation digests, and parameter digests,
- generated artifact paths, sizes, rows, schemas, and content digests,
- parent dataset IDs for derived data,
- a time range and quality-report artifact,
- the reproduction policy,
- optional legacy-manifest evidence for migrations.

A transform input must reference a source object, an artifact, or a parent dataset through `dataset:<dataset_id>`. An output must reference an artifact declared in the same manifest, and one artifact cannot be produced by multiple transforms.

## Dataset Registry and lineage

`DatasetRegistry` is immutable. Registering the same dataset ID with identical content is idempotent; registering the same ID with different content fails. Parent datasets must already exist, and lineage cycles are rejected.

The registry exposes:

- exact lookup by dataset ID,
- ordered ancestors and descendants,
- source IDs for one dataset,
- reverse lookup by source digest,
- deterministic JSON and a registry content SHA-256.

A derived dataset can therefore be traced from an experiment output back through every parent dataset and immutable upstream source.

## Reproduction policy

Every dataset and experiment output declares one of two policies:

### `EXACT_BYTES`

The regenerated file must match the committed digest byte-for-byte. Numeric tolerance is forbidden.

Use this for canonical CSV, JSON, manifests, decision packages, and reports whose serialization is controlled.

### `NUMERIC_TOLERANCE`

The caller must declare an absolute tolerance, a relative tolerance, or both. NaN equality is explicit. A tolerance-free numeric comparison is invalid.

Use this only when a runtime, hardware backend, or third-party implementation cannot guarantee byte identity and the permitted difference is scientifically justified in advance.

## Experiment Manifest

`ExperimentManifest` pins:

- one or more registered dataset IDs,
- code revisions,
- dependency lock revisions,
- rule revisions,
- environment revisions,
- optional config revisions,
- a fixed seed or explicit `DETERMINISTIC_NO_RNG`,
- expected outputs and their reproduction policies.

The manifest rejects missing code, dependency, rule, or environment evidence. A Git revision uses a 40-character SHA and its visible version must equal that SHA. `validate_registry()` rejects experiments that reference datasets not registered in the supplied registry.

## Ownership boundary

`quant-platform` owns reusable contracts, validation, lineage traversal, deterministic serialization, and reproduction policies.

`quant-alpha` owns concrete source lists, proprietary experiment revisions, strategy configs, and adapters that convert legacy Momentum and Funding manifests into the public contracts.

`quant-ops` later pins the approved registry and experiment-manifest hashes used by a release.
