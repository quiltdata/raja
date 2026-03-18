from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from raja.datazone import DataZoneConfig

_REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SEED_CONFIG_PATH = _REPO_ROOT / "seed-config.yaml"
DEFAULT_SEED_STATE_PATH = _REPO_ROOT / ".rale-seed-state.json"
DEFAULT_TEST_URI_PATH = _REPO_ROOT / ".rale-test-uri"


@dataclass(frozen=True)
class SeedProject:
    key: str
    display_name: str
    slot: str
    designation: str = "PROJECT_CONTRIBUTOR"


@dataclass(frozen=True)
class SeedPackage:
    name: str
    producer_project: str
    consumer_project: str


@dataclass(frozen=True)
class SeedUserAssignment:
    username: str
    project_key: str
    designation: str


@dataclass(frozen=True)
class SeedConfig:
    projects: tuple[SeedProject, ...]
    packages: tuple[SeedPackage, ...]
    default_project: str

    def project(self, key: str) -> SeedProject:
        for project in self.projects:
            if project.key == key:
                return project
        raise KeyError(f"Unknown seed project: {key}")

    def package_by_name(self, name: str) -> SeedPackage:
        for package in self.packages:
            if package.name == name:
                return package
        raise KeyError(f"Unknown seed package: {name}")

    def package_for_home_project(self, project_key: str) -> SeedPackage:
        for package in self.packages:
            if package.producer_project == project_key:
                return package
        raise KeyError(f"No home package for project: {project_key}")

    def package_for_consumer_project(self, project_key: str) -> SeedPackage:
        for package in self.packages:
            if package.consumer_project == project_key:
                return package
        raise KeyError(f"No foreign package for project: {project_key}")

    def package_for_inaccessible_project(self, project_key: str) -> SeedPackage:
        for package in self.packages:
            if package.producer_project != project_key and package.consumer_project != project_key:
                return package
        raise KeyError(f"No inaccessible package for project: {project_key}")

    def project_id_map(self, datazone_config: DataZoneConfig) -> dict[str, str]:
        return {
            project.key: datazone_config.slot(project.slot).project_id for project in self.projects
        }

    def slot_label_map(self) -> dict[str, str]:
        return {project.slot: project.display_name for project in self.projects}


def load_seed_config(path: Path = DEFAULT_SEED_CONFIG_PATH) -> SeedConfig:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid seed config in {path}")

    raw_projects = payload.get("projects") or []
    raw_packages = payload.get("packages") or []
    if not isinstance(raw_projects, list) or not isinstance(raw_packages, list):
        raise ValueError(f"Invalid seed config collections in {path}")

    projects = tuple(
        SeedProject(
            key=str(item["key"]),
            display_name=str(item["display_name"]),
            slot=str(item["slot"]),
            designation=str(item.get("designation") or "PROJECT_CONTRIBUTOR"),
        )
        for item in raw_projects
        if isinstance(item, dict)
    )
    packages = tuple(
        SeedPackage(
            name=str(item["name"]),
            producer_project=str(item["producer_project"]),
            consumer_project=str(item["consumer_project"]),
        )
        for item in raw_packages
        if isinstance(item, dict)
    )
    if not projects or not packages:
        raise ValueError(f"Seed config must define projects and packages: {path}")
    default_project = str(payload.get("default_project") or projects[0].key)
    return SeedConfig(projects=projects, packages=packages, default_project=default_project)


def build_user_assignments(usernames: list[str], config: SeedConfig) -> list[SeedUserAssignment]:
    assignments: list[SeedUserAssignment] = []
    for index, username in enumerate(usernames):
        project = config.projects[index % len(config.projects)]
        assignments.append(
            SeedUserAssignment(
                username=username,
                project_key=project.key,
                designation=project.designation,
            )
        )
    return assignments


def load_seed_state(path: Path = DEFAULT_SEED_STATE_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        return {}
    return payload


def write_seed_state(state: dict[str, Any], path: Path = DEFAULT_SEED_STATE_PATH) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
