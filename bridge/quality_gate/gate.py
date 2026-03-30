"""Quality gate command builder.

AOF Quality Control System (5 layers):
- L1: Text Integrity (encoding, empty files)
- L2: Syntax Correctness (JSON/YAML/Python)
- L3: Data Integrity (required fields, constraints)
- L4: Business Logic (reserved for future)
- L5: Cross-Phase Consistency (reserved for future)

Usage:
    from bridge.quality_gate import quality_gate_commands
    cmds = quality_gate_commands("/path/to/aof")
"""

from __future__ import annotations

from pathlib import Path


def quality_gate_commands(project_root: str) -> list[str]:
    """Return quality gate commands for the project.
    
    Returns commands for L1-L3 quality checks.
    L4 and L5 are reserved for future implementation.
    """
    root = Path(project_root)
    
    # Use unified quality gate entry
    unified_gate = f"python {root / 'tools' / 'quality_gate' / 'run_all_lints.py'} --quick"
    
    # Legacy commands (kept for compatibility)
    legacy = [
        f"python {root / 'tools' / 'quality_gate' / 'lint_l1_text_integrity.py'}",
        f"python {root / 'tools' / 'quality_gate' / 'lint_l2_syntax.py'}",
        f"python {root / 'tools' / 'quality_gate' / 'lint_l3_data_integrity.py'}",
    ]
    
    return [unified_gate] + legacy


def run_quality_gate(project_root: str, level: str = "quick") -> dict:
    """Run quality gate and return results.
    
    Args:
        project_root: Path to AOF project root
        level: 'quick' for L1-L3, 'full' for all layers
    
    Returns:
        Dict with 'success', 'errors', 'warnings' keys
    """
    import subprocess
    import sys
    
    root = Path(project_root)
    script = root / 'tools' / 'quality_gate' / 'run_all_lints.py'
    
    cmd = [sys.executable, str(script)]
    if level == "quick":
        cmd.append('--quick')
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        return {
            'success': result.returncode == 0,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }
