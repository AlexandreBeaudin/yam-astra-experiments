"""Draft boundaries, refined during M1/M2. No third-party dependencies."""

from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


class DatasetAdapter(Protocol):
    def audit(self, source: Path) -> Mapping[str, Any]: ...

    def normalize(self, source: Path, destination: Path) -> Path:
        """Return the canonical episode manifest, preserving provenance."""
        ...


class RobotAdapter(Protocol):
    def describe(self, profile: Path) -> Mapping[str, Any]: ...

    def map_joints(self, source: Mapping[str, float]) -> Mapping[str, float]: ...


class AssetBuilder(Protocol):
    def build(self, recipe: Path, destination: Path) -> Path:
        """Return an asset manifest, including visual/collision separation."""
        ...


class Backend(Protocol):
    def capabilities(self) -> Mapping[str, Any]: ...

    def load_scene(self, scene_manifest: Path) -> None: ...

    def set_state(self, state: Mapping[str, Any]) -> None:
        """Initialization or visual-state replay; not an action rollout."""
        ...

    def apply_control(self, command: Mapping[str, Any]) -> None: ...

    def step(self, dt_s: float) -> None: ...

    def read_state(self) -> Mapping[str, Any]: ...

    def render(self, camera_ids: Sequence[str], destination: Path) -> Mapping[str, Path]: ...

    def close(self) -> None: ...


class Evaluator(Protocol):
    def evaluate(self, reference: Path, replay: Path, split: Path) -> Mapping[str, Any]: ...
