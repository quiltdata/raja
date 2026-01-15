"""Platform detection utilities for CDK deployments."""

from __future__ import annotations

import platform as py_platform

from aws_cdk.aws_ecr_assets import Platform as DockerPlatform
from aws_cdk.aws_ecs import CpuArchitecture
from aws_cdk.aws_lambda import Architecture as LambdaArchitecture


def detect_platform() -> tuple[CpuArchitecture, DockerPlatform, LambdaArchitecture]:
    """
    Detect current machine architecture and return CDK platform objects.

    Returns:
        Tuple of (ECS CpuArchitecture, Docker Platform, Lambda Architecture)

    Raises:
        RuntimeError: If architecture is unsupported
    """
    machine = py_platform.machine().lower()

    if machine in ("aarch64", "arm64"):
        return (
            CpuArchitecture.ARM64,
            DockerPlatform.LINUX_ARM64,
            LambdaArchitecture.ARM_64,
        )
    elif machine in ("x86_64", "amd64"):
        return (
            CpuArchitecture.X86_64,
            DockerPlatform.LINUX_AMD64,
            LambdaArchitecture.X86_64,
        )
    else:
        raise RuntimeError(
            f"Unsupported architecture: {machine}. "
            "Supported: ARM64 (aarch64, arm64) or x86_64 (amd64, x86_64)"
        )


def get_platform_string() -> str:
    """Get human-readable platform string for logging."""
    machine = py_platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        return "ARM64 (linux/arm64)"
    elif machine in ("x86_64", "amd64"):
        return "x86_64 (linux/amd64)"
    else:
        return f"Unknown ({machine})"
