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
                "ai_evaluation_explanation": {"explanation": obj.get("explanation", "")},
            }
        
        tasks = [_grade_one(s, o) for s, o in zip(samples, outputs)]
        return await asyncio.gather(*tasks)


class AtomicCorrectnessMetric(BaseMetric):
    def __init__(self, judge_model):
        if judge_model is None:
            raise ValueError("AtomicCorrectnessMetric requires a judge_model (LLM)")
        super().__init__(name="correctness_atomic", judge_model=judge_model)
    
    async def evaluate(self, samples, outputs):
        async def _grade_one(sample, output):
            # Step 1: Extract atomic facts from reference answer
            extract_prompt = f"""
You are analyzing a reference answer to identify atomic facts.

Definition of an atomic fact:
A minimal, self-contained, non-decomposable factual statement that conveys exactly one verifiable unit of information such that:
- It cannot be broken into smaller facts without losing meaning
- It contains exactly one claim that can be independently supported or contradicted by retrieved context
- It is fully evaluable (true/false/not-answerable) based on provided evidence

Question:
{sample.input}

Reference answer:
{sample.human_reference_answer}

Extract all atomic facts from the reference answer. Return JSON with:
- atomic_facts: a list of strings, where each string is one atomic fact

Example format:
{{
  "atomic_facts": [
    "Fact 1: ...",
    "Fact 2: ...",
    "Fact 3: ..."
  ]
}}
"""
            extract_resp = await self.judge_model.ainvoke(extract_prompt)
            raw_content = extract_resp.content if hasattr(extract_resp, "content") else str(extract_resp)
            text_content = extract_text_content(raw_content)
            json_text = extract_json_from_text(text_content)
            
            try:
                extract_obj = json.loads(json_text)
                atomic_facts = extract_obj.get("atomic_facts", [])
            except json.JSONDecodeError:
                # Fallback: treat as no atomic facts found
                atomic_facts = []
            
            if not atomic_facts:
                # No atomic facts extracted, return score of 0
                return {
                    "id": sample.sample_id,
                    "metric": self.name,
                    "score": 0.0,
                    "ai_evaluation_explanation": {
                        "atomic_facts_count": 0,
                        "atomic_facts_found": 0,
                        "explanation": "Failed to extract atomic facts from reference answer",
                        "atomic_facts": [],
                    },
                }
            
            # Step 2: Check each atomic fact separately with individual LLM calls
            async def _check_one_fact(fact_index, atomic_fact):
                """Check if a single atomic fact is present in the AI answer."""
                check_prompt = f"""
You are checking if a specific atomic fact from the reference answer is present in the AI answer.

Question:
{sample.input}

Reference answer:
{sample.human_reference_answer}

AI answer:
{output["answer"]}

Atomic fact to check:
{atomic_fact}

Determine if this atomic fact is present in the AI answer. An atomic fact is considered present if:
- The AI answer contains the same factual claim (even if worded differently)
- The AI answer supports or confirms the atomic fact
- The information is clearly conveyed, not just implied

Return JSON with:
- found: true or false (boolean indicating if the atomic fact is present)
- explanation: a brief explanation of why the fact was or was not found

Example format:
{{
  "found": true,
  "explanation": "The AI answer states that..."
}}
"""
                check_resp = await self.judge_model.ainvoke(check_prompt)
                raw_content = check_resp.content if hasattr(check_resp, "content") else str(check_resp)
                text_content = extract_text_content(raw_content)
                json_text = extract_json_from_text(text_content)
                
                try:
                    check_obj = json.loads(json_text)
                    found = bool(check_obj.get("found", False))
                    explanation = check_obj.get("explanation", "")
                except json.JSONDecodeError:
                    # Fallback: treat as not found
                    found = False
                    explanation = "Failed to parse judge response for this atomic fact."
                
                return {
                    "fact_index": fact_index,
                    "atomic_fact": atomic_fact,
                    "found": found,
                    "explanation": explanation,
                }
            
            # Evaluate all atomic facts in parallel
            fact_evaluations = await asyncio.gather(*[
                _check_one_fact(i, fact) for i, fact in enumerate(atomic_facts)
            ])
            
            # Sort by fact_index to ensure order matches atomic_facts
            fact_evaluations.sort(key=lambda x: x["fact_index"])
            
            # Calculate score: number of facts found / total number of facts
            facts_found = sum(1 for eval_result in fact_evaluations if eval_result["found"])
            total_facts = len(atomic_facts)
            score = facts_found / total_facts if total_facts > 0 else 0.0
            
            return {
                "id": sample.sample_id,
                "metric": self.name,
                "score": float(score),
                "ai_evaluation_explanation": {
                    "atomic_facts_count": total_facts,
                    "atomic_facts_found": facts_found,
                    "fact_evaluations": fact_evaluations,
                },
            }
        
        tasks = [_grade_one(s, o) for s, o in zip(samples, outputs)]
        return await asyncio.gather(*tasks)

