"""Small explicit registry used by applications to assemble plugins."""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import AlphaPlugin


@dataclass(slots=True)
class PluginRegistry:
    _alphas: dict[str, AlphaPlugin] = field(default_factory=dict)

    def register_alpha(self, plugin: AlphaPlugin) -> None:
        if not plugin.name:
            raise ValueError("plugin name must not be empty")
        if plugin.name in self._alphas:
            raise ValueError(f"alpha plugin already registered: {plugin.name}")
        self._alphas[plugin.name] = plugin

    def get_alpha(self, name: str) -> AlphaPlugin:
        try:
            return self._alphas[name]
        except KeyError as exc:
            raise KeyError(f"unknown alpha plugin: {name}") from exc

    def alpha_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._alphas))
