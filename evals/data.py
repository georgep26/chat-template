# evals/data.py

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import pandas as pd


@dataclass
class EvalSample:
    """Represents a single evaluation sample."""
    sample_id: str
    input: str
    human_reference_answer: str
    human_reference_citation: Optional[str]
    source: Optional[str]  # "human" or "ai" - indicates source of question and reference answer
    metadata: Dict[str, Any]


@dataclass
class JudgeValidationSample:
    """Represents a single judge validation sample."""
    validation_sample_id: str
    input: str
    human_reference_answer: str
    human_reference_citation: Optional[str]
    judge_score: Optional[float] = None
    judge_explanation: Optional[str] = None
    human_score: Optional[float] = None
    human_explanation: Optional[str] = None


def load_eval_dataframe(config: dict) -> pd.DataFrame:
    data_cfg = config["data"]
    df = pd.read_csv(data_cfg["eval_csv_path"])
    
    required = [
        data_cfg["eval_question_column"],
        data_cfg["eval_reference_column"],
    ]
    
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing column {col} in eval CSV")
    
    return df


def extract_eval_samples(df: pd.DataFrame, config: dict) -> List[EvalSample]:
    data_cfg = config["data"]
    id_col = data_cfg.get("eval_id_column")
    q_col = data_cfg["eval_question_column"]
    ref_col = data_cfg["eval_reference_column"]
    citation_col = data_cfg.get("eval_citation_column")
    source_col = data_cfg.get("eval_source_column")
    
    samples = []
    for idx, row in df.iterrows():
        sample_id = row[id_col] if id_col and id_col in df.columns else str(idx)
        input_text = row[q_col]
        reference_answer = row[ref_col]
        citation = row[citation_col] if citation_col and citation_col in df.columns else None
        source = row[source_col] if source_col and source_col in df.columns else None
        
        # everything else goes into metadata
        metadata = row.to_dict()
        for key in [id_col, q_col, ref_col, citation_col, source_col]:
            if key and key in metadata:
                metadata.pop(key, None)
        
        samples.append(EvalSample(
            sample_id=sample_id,
            input=input_text,
            human_reference_answer=reference_answer,
            human_reference_citation=citation,
            source=source,
            metadata=metadata,
        ))
    
    return samples


def load_judge_validation_dataframe(config: dict) -> pd.DataFrame:
    jcfg = config["judge_validation"]
    df = pd.read_csv(jcfg["csv_path"])
    
    for col in [
        jcfg["question_column"],
        jcfg["reference_column"],
        jcfg["model_answer_column"],
        jcfg["human_label_column"],
    ]:
        if col not in df.columns:
            raise ValueError(f"Missing column {col} in judge-validation CSV")
    
    return df


def extract_judge_validation_samples(df: pd.DataFrame, config: dict) -> List[JudgeValidationSample]:
    """Extract judge validation samples from dataframe."""
    jcfg = config["judge_validation"]
    id_col = jcfg.get("id_column")
    q_col = jcfg["question_column"]
    ref_col = jcfg["reference_column"]
    citation_col = jcfg.get("reference_citation_column")
    human_label_col = jcfg["human_label_column"]
    human_explanation_col = jcfg.get("human_explanation_column")
    
    samples = []
    for idx, row in df.iterrows():
        validation_sample_id = row[id_col] if id_col and id_col in df.columns else str(idx)
        input_text = row[q_col]
        reference_answer = row[ref_col]
        citation = row[citation_col] if citation_col and citation_col in df.columns else None
        human_score = float(row[human_label_col]) if pd.notna(row[human_label_col]) else None
        human_explanation = row[human_explanation_col] if human_explanation_col and human_explanation_col in df.columns and pd.notna(row[human_explanation_col]) else None
        
        samples.append(JudgeValidationSample(
            validation_sample_id=validation_sample_id,
            input=input_text,
            human_reference_answer=reference_answer,
            human_reference_citation=citation,
            human_score=human_score,
            human_explanation=human_explanation,
        ))
    
    return samples

