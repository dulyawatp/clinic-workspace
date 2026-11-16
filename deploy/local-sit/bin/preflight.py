#!/usr/bin/env python3
"""Refuse to deploy the isolated stack onto resources this project does not own.

Forces the Compose project name (`-p marley-sit`) because an inherited
`COMPOSE_PROJECT_NAME` must never redirect this deployment. Then checks that:

  * the resolved Compose model name is the forced project name;
  * every existing container and network labelled with that project was created
    from this exact compose file;
  * the four expected volumes either do not exist or are labelled as this
    project's volumes (an unlabelled or foreign volume with a colliding name is
    refused);
  * the published loopback port, if already bound, belongs to this project;
  * no other Compose project claims this project name.

Read-only: it never starts, stops, deletes or recreates anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT = "marley-sit"
REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = (REPO_ROOT / "deploy/local-sit/compose.yaml").resolve()
EXPECTED_VOLUMES = {
    "marley-sit-sites",
    "marley-sit-logs",
    "marley-sit-db-data",
    "marley-sit-redis-queue-data",
}
EXPECTED_NETWORK = f"{PROJECT}_default"
PUBLISHED_PORT = "18081"

failures: list[str] = []


def ok(condition: bool, message: str, detail: object = None) -> None:
    state = "PASS" if condition else "REFUSE"
    suffix = f": {detail}" if detail is not None else ""
    print(f"{state} {message}{suffix}")
    if not condition:
        failures.append(message)


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def main() -> int:
    inherited = os.environ.get("COMPOSE_PROJECT_NAME")
    print(
        f"INFO COMPOSE_PROJECT_NAME={inherited!r} (ignored; -p {PROJECT} is forced)"
        if inherited
        else f"INFO COMPOSE_PROJECT_NAME not set; using -p {PROJECT}"
    )

    rendered = compose("config", "--format", "json")
    if rendered.returncode != 0:
        print("REFUSE compose model does not render")
        return 1
    model = json.loads(rendered.stdout)
    ok(model.get("name") == PROJECT, "resolved Compose project is forced name", model.get("name"))

    project_containers = docker(
        "ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"
    ).stdout.split()
    for container_id in project_containers:
        labels = json.loads(
            docker("inspect", "--format", "{{json .Config.Labels}}", container_id).stdout
        )
        config_files = labels.get("com.docker.compose.project.config_files", "")
        resolved = [os.path.realpath(p) for p in config_files.split(",") if p]
        ok(
            str(COMPOSE_FILE) in resolved,
            "container belongs to this compose file",
            {
                "service": labels.get("com.docker.compose.service"),
                "project": labels.get("com.docker.compose.project"),
            },
        )

    project_volumes = docker(
        "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"
    ).stdout.split()
    ok(
        set(project_volumes) <= EXPECTED_VOLUMES,
        "no unexpected volumes carry this project label",
        sorted(project_volumes),
    )

    all_volumes = set(docker("volume", "ls", "-q").stdout.split())
    for name in sorted(EXPECTED_VOLUMES & all_volumes):
        labels = json.loads(
            docker("volume", "inspect", "--format", "{{json .Labels}}", name).stdout
        ) or {}
        ok(
            labels.get("com.docker.compose.project") == PROJECT,
            "expected volume is owned by this project",
            {"volume": name, "project": labels.get("com.docker.compose.project")},
        )

    project_networks = docker(
        "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"
    ).stdout.split()
    for network_id in project_networks:
        network = json.loads(docker("network", "inspect", network_id).stdout)[0]
        labels = network.get("Labels") or {}
        ok(
            labels.get("com.docker.compose.project") == PROJECT,
            "network is owned by this project",
            {"name": network.get("Name")},
        )
    if EXPECTED_NETWORK in set(docker("network", "ls", "--format", "{{.Name}}").stdout.split()):
        labels = json.loads(
            docker("network", "inspect", "--format", "{{json .Labels}}", EXPECTED_NETWORK).stdout
        ) or {}
        ok(
            labels.get("com.docker.compose.project") == PROJECT,
            "expected network name is not owned by a foreign project",
            {"network": EXPECTED_NETWORK},
        )

    listeners = docker("ps", "-q", "--filter", f"publish={PUBLISHED_PORT}").stdout.split()
    for container_id in listeners:
        labels = json.loads(
            docker("inspect", "--format", "{{json .Config.Labels}}", container_id).stdout
        )
        ok(
            labels.get("com.docker.compose.project") == PROJECT,
            "loopback port listener belongs to this project",
            {"port": PUBLISHED_PORT, "project": labels.get("com.docker.compose.project")},
        )

    projects = json.loads(docker("compose", "ls", "-a", "--format", "json").stdout) or []
    for entry in projects:
        if entry.get("Name") != PROJECT:
            continue
        config_files = [
            os.path.realpath(p) for p in (entry.get("ConfigFiles") or "").split(",") if p
        ]
        ok(
            str(COMPOSE_FILE) in config_files,
            "registered Compose project uses this compose file",
            {"project": PROJECT},
        )

    if failures:
        print(f"REFUSING DEPLOYMENT: {len(failures)} ownership collision(s)")
        return 1
    print("PREFLIGHT OK: project and resources are owned by marley-sit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
