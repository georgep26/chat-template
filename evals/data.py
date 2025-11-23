# evals/data.py

import pandas as pd


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


def extract_eval_samples(df: pd.DataFrame, config: dict):
    data_cfg = config["data"]
    id_col = data_cfg.get("eval_id_column")
    q_col = data_cfg["eval_question_column"]
    ref_col = data_cfg["eval_reference_column"]
    
    samples = []
    for idx, row in df.iterrows():
        sample_id = row[id_col] if id_col and id_col in df.columns else str(idx)
        question = row[q_col]
        reference = row[ref_col]
        
        # everything else goes into metadata
        metadata = row.to_dict()
        for key in [id_col, q_col, ref_col]:
            if key in metadata:
                metadata.pop(key, None)
        
        samples.append({
            "id": sample_id,
            "question": question,
            "reference_answer": reference,
            "metadata": metadata,
        })
    
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

