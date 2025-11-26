# evals/metrics_custom.py

import json
import re
import asyncio
from typing import Any, Dict, List, Union
from metrics_base import BaseMetric


def extract_text_content(content: Union[str, List[Dict[str, Any]]]) -> str:
    """
    Extract text content from a message content field.
    
    Handles both string format and list format (e.g., [{'type': 'text', 'text': '...'}]).
    
    Args:
        content: Message content, either a string or a list of content blocks
        
    Returns:
        Extracted text as a string
    """
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        # Handle list format like [{'type': 'text', 'text': '...'}]
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    text_parts.append(block["text"])
                elif "text" in block:
                    text_parts.append(block["text"])
        return "".join(text_parts)
    else:
        # Fallback: convert to string
        return str(content)


def extract_json_from_text(text: str) -> str:
    """
    Extract JSON from text that may be wrapped in markdown code blocks.
    
    Args:
        text: Text that may contain JSON wrapped in ```json ... ``` blocks
        
    Returns:
        Extracted JSON string
    """
    # Try to find JSON in markdown code blocks first
    json_block_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
    match = re.search(json_block_pattern, text, re.DOTALL)
    if match:
        return match.group(1)
    
    # If no code block, try to find JSON object directly
    json_object_pattern = r'\{.*\}'
    match = re.search(json_object_pattern, text, re.DOTALL)
    if match:
        return match.group(0)
    
    # Fallback: return the text as-is
    return text


class BinaryCorrectnessMetric(BaseMetric):
    def __init__(self, judge_model):
        if judge_model is None:
            raise ValueError("BinaryCorrectnessMetric requires a judge_model (LLM)")
        super().__init__(name="correctness_binary", judge_model=judge_model)
    
    async def evaluate(self, samples, outputs):
        async def _grade_one(sample, output):
            prompt = f"""
You are grading the factual correctness of the model answer
compared to the reference answer.

Question:
{sample.input}

Reference answer:
{sample.human_reference_answer}

Model answer:
{output["answer"]}

Return JSON with:
- score: 0 or 1
- explanation: short explanation
"""
            # LangChain LLM call (async)
            resp = await self.judge_model.ainvoke(prompt)
            
            # Extract text content (handles both string and list formats)
            raw_content = resp.content if hasattr(resp, "content") else str(resp)
            text_content = extract_text_content(raw_content)
            
            # Extract JSON from text (handles markdown code blocks)
            json_text = extract_json_from_text(text_content)
            
            # Parse JSON
            try:
                obj = json.loads(json_text)
            except json.JSONDecodeError:
                # fallback: heuristic; treat any non-parse as 0
                obj = {"score": 0, "explanation": f"Failed to parse judge response. Raw content: {text_content[:200]}"}
            
            return {
                "id": sample.sample_id,
                "metric": self.name,
                "score": float(obj.get("score", 0)),
                "extra": {"explanation": obj.get("explanation", "")},
            }
        
        tasks = [_grade_one(s, o) for s, o in zip(samples, outputs)]
        return await asyncio.gather(*tasks)

