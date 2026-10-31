"""DualPath：失败先行、证据驱动的任务校验框架。"""

from .models import (
    ErrorPathResult,
    Evidence,
    Finding,
    Problem,
    ReviewResult,
    RunResult,
    classify_problems,
)
from .orchestrator import DualPath
from .review import ReviewEngine
from .verification import Completion, VerificationResult, VerifiedDualPath

__all__ = [
    "DualPath",
    "ReviewEngine",
    "ReviewResult",
    "Finding",
    "VerifiedDualPath",
    "VerificationResult",
    "Completion",
    "ErrorPathResult",
    "Evidence",
    "Problem",
    "RunResult",
    "classify_problems",
]

__version__ = "0.2.0"
