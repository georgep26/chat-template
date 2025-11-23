# evals/stats_utils.py

import numpy as np
from typing import Dict, List


def aggregate_metric(scores: List[float], ci: float = 0.95) -> Dict[str, float]:
    arr = np.array(scores, dtype=float)
    
    if arr.size == 0:
        return {
            "mean": float("nan"),
            "std": float("nan"),
            "median": float("nan"),
            "min": float("nan"),
            "max": float("nan"),
            "ci_lower": float("nan"),
            "ci_upper": float("nan"),
        }
    
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    median = float(np.median(arr))
    min_val = float(arr.min())
    max_val = float(arr.max())
    
    # bootstrap CI on the mean
    n_boot = 1000
    rng = np.random.default_rng(42)
    boot_means = []
    for _ in range(n_boot):
        sample = rng.choice(arr, size=arr.size, replace=True)
        boot_means.append(sample.mean())
    
    lower = float(np.percentile(boot_means, (1 - ci) / 2 * 100))
    upper = float(np.percentile(boot_means, (1 + ci) / 2 * 100))
    
    return {
        "mean": mean,
        "std": std,
        "median": median,
        "min": min_val,
        "max": max_val,
        "ci_lower": lower,
        "ci_upper": upper,
    }

