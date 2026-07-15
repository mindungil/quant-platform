# Open-Core Architecture

## Trust boundaries

The project is split by responsibility rather than by deployment convenience.

| Repository | Visibility | Owns | Must not own |
|---|---|---|---|
| `quant-platform` | Public | Stable contracts, reusable engines, examples, tests | Proprietary strategies, production topology, runtime data |
| `quant-alpha` | Private | Strategy, factor, portfolio and execution extensions | Host inventory, credentials, deployment manifests |
| `quant-ops` | Private | Version pinning, deployment, monitoring, backup and runbooks | Business logic and importable application modules |
| `quant-platform-internal` | Private | Historical integrated implementation and migration source | New public history |

## Dependency rule

```text
quant-platform <- quant-alpha <- quant-ops
```

The arrow means “is depended on by.” The public package never imports a private package. Operations assembles artifacts and configuration but is not an application dependency.

## Plugin model

The public package publishes small protocols and immutable data contracts. Private implementations are registered explicitly by an application composition root. Import-time discovery, hidden global registration, and service-to-service imports are avoided because they make the boundary difficult to audit.

## Determinism

Trading decisions and risk gates must be deterministic for a fixed input, code revision, and configuration. Language models may produce explanations or research assistance, but they are not the authoritative source of order decisions.

## Runtime separation

Generated market data, model artifacts, backtest results, logs, portfolio state, credentials, host inventory, and operator transcripts are runtime material. They are stored outside source control and backed up according to the private operations runbooks.

## Migration policy

Generic modules from `quant-platform-internal` may enter the public repository only after:

1. removing environment-specific assumptions and proprietary parameters;
2. replacing private imports with public protocols;
3. adding deterministic tests and documentation;
4. passing the public-boundary scanner;
5. entering through the new repository history rather than copying old Git history.
