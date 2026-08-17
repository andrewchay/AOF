"""Compiler plugin contracts for AOF Semantic IR releases."""

from .base import (
    CompiledArtifact,
    CompilePlan,
    CompileStep,
    CompilationInput,
    CompilerError,
    CompilerRegistry,
    SemanticCompiler,
    VerificationReport,
)
from .semantic_bundle import SemanticBundleCompiler
from .governance import (
    CompilationWaiver,
    CompilerPolicy,
    CompilerPolicyError,
    CompilerPolicyFinding,
    CompilerPolicyReport,
)
from .runtime_targets import DatalogCompiler, McpCompiler, OwlCompiler, RagCompiler, ShaclCompiler
from .control_plane import CompilerControlPlane, CompilerControlPlaneError
from .runs import (
    CompilationRun,
    CompilationRunError,
    CompilationRunRepository,
    CompilationRunService,
)


def default_compiler_registry() -> CompilerRegistry:
    """Return the complete deterministic P0.5 compiler set."""
    return CompilerRegistry([
        SemanticBundleCompiler(),
        OwlCompiler(),
        ShaclCompiler(),
        DatalogCompiler(),
        RagCompiler(),
        McpCompiler(),
    ])

__all__ = [
    "CompiledArtifact",
    "CompilePlan",
    "CompileStep",
    "CompilationInput",
    "CompilationRun",
    "CompilationRunError",
    "CompilationRunRepository",
    "CompilationRunService",
    "CompilationWaiver",
    "CompilerError",
    "CompilerControlPlane",
    "CompilerControlPlaneError",
    "CompilerRegistry",
    "CompilerPolicy",
    "CompilerPolicyError",
    "CompilerPolicyFinding",
    "CompilerPolicyReport",
    "SemanticCompiler",
    "SemanticBundleCompiler",
    "OwlCompiler",
    "ShaclCompiler",
    "DatalogCompiler",
    "RagCompiler",
    "McpCompiler",
    "default_compiler_registry",
    "VerificationReport",
]
