"""DualPath：直接解决与错误路线并行推进，最终共同汇聚。"""

from .models import ErrorPathResult, Evidence, Problem, RunResult, classify_problems
from .orchestrator import DualPath
from .verification import Completion, VerifiedDualPath, VerificationResult

__all__ = [
    "DualPath",
    "VerifiedDualPath",
    "VerificationResult",
    "Completion",
    "ErrorPathResult",
    "Evidence",
    "Problem",
    "RunResult",
    "classify_problems",
]

__version__ = "0.1.0"
