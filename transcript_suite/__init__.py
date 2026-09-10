"""
Transcript Suite - High performance speech transcription powered by NVIDIA Canary-Qwen-2.5B.
"""

import os

# Prevent CUDA memory fragmentation on 8GB GPUs
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

__version__ = "0.1.0"
