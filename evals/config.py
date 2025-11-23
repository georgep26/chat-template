# evals/config.py

import yaml


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    
    # light sanity checks / defaults
    cfg.setdefault("outputs", {})
    cfg["outputs"].setdefault("types", ["html", "json", "csv"])
    cfg.setdefault("run", {})
    cfg["run"].setdefault("max_concurrency", 10)
    
    return cfg

