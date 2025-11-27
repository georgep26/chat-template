# evals/client.py

import importlib
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
import boto3
import uuid

_executor = ThreadPoolExecutor(max_workers=32)


class BaseRagClient:
    def __init__(self, rag_app_cfg: dict):
        self.cfg = rag_app_cfg
    
    async def generate(self, sample) -> dict:
        raise NotImplementedError
    
    async def generate_batch(self, samples, max_concurrency: int):
        sem = asyncio.Semaphore(max_concurrency)
        
        async def _wrapped(s):
            async with sem:
                return await self.generate(s)
        
        return await asyncio.gather(*[_wrapped(s) for s in samples])


class LocalRagClient(BaseRagClient):
    def __init__(self, rag_app_cfg: dict):
        super().__init__(rag_app_cfg)
        self._handler = self._load_entrypoint(rag_app_cfg["local_entrypoint"])
    
    @staticmethod
    def _load_entrypoint(entrypoint: str):
        module_name, func_name = entrypoint.split(":")
        module = importlib.import_module(module_name)
        return getattr(module, func_name)
    
    async def generate(self, sample) -> dict:
        loop = asyncio.get_running_loop()
        
        # Adapt request format: input -> message, add conversation_id and user_id
        event_body = {
            "message": sample.input,
            "conversation_id": sample.metadata.get("conversation_id", str(uuid.uuid4())),
            "user_id": sample.metadata.get("user_id", "eval_user"),
        }
        
        # Add retrieval_filters if present in metadata
        if "retrieval_filters" in sample.metadata:
            event_body["retrieval_filters"] = sample.metadata["retrieval_filters"]
        
        def _call():
            # The main function expects event_body dict and returns a response dict
            # Response format: {"statusCode": 200, "headers": {...}, "body": json_string}
            resp = self._handler(event_body)
            
            # Parse the body if it's a string (from model_dump_json())
            if isinstance(resp, dict) and "body" in resp:
                if isinstance(resp["body"], str):
                    body = json.loads(resp["body"])
                else:
                    body = resp["body"]
            else:
                body = resp
            
            return body
        
        resp = await loop.run_in_executor(_executor, _call)
        
        # Extract answer and contexts from ChatResponse format
        # Response has: answer (str), sources (List[Source] where Source has chunk)
        answer = resp.get("answer", "")
        sources = resp.get("sources", [])
        
        # Convert sources to contexts list
        contexts = [source.get("chunk", "") for source in sources if isinstance(source, dict)]
        
        return {
            "answer": answer,
            "contexts": contexts,
            "raw": resp,
        }


class LambdaRagClient(BaseRagClient):
    def __init__(self, rag_app_cfg: dict):
        super().__init__(rag_app_cfg)
        self._lambda = boto3.client("lambda")
    
    async def generate(self, sample) -> dict:
        loop = asyncio.get_running_loop()
        
        # Adapt request format: input -> message, add conversation_id and user_id
        payload = {
            "message": sample.input,
            "conversation_id": sample.metadata.get("conversation_id", str(uuid.uuid4())),
            "user_id": sample.metadata.get("user_id", "eval_user"),
        }
        
        # Add retrieval_filters if present in metadata
        if "retrieval_filters" in sample.metadata:
            payload["retrieval_filters"] = sample.metadata["retrieval_filters"]
        
        def _invoke():
            resp = self._lambda.invoke(
                FunctionName=self.cfg["lambda_function_name"],
                InvocationType="RequestResponse",
                Payload=json.dumps(payload).encode("utf-8"),
            )
            body = resp["Payload"].read()
            return json.loads(body)
        
        resp = await loop.run_in_executor(_executor, _invoke)
        
        # Handle Lambda response format (may have statusCode/body or be direct)
        if isinstance(resp, dict) and "statusCode" in resp:
            if resp["statusCode"] != 200:
                raise RuntimeError(f"Lambda error: {resp.get('body', 'Unknown error')}")
            body = json.loads(resp["body"]) if isinstance(resp.get("body"), str) else resp.get("body", {})
        else:
            body = resp
        
        # Extract answer and contexts from ChatResponse format
        answer = body.get("answer", "")
        sources = body.get("sources", [])
        
        # Convert sources to contexts list
        contexts = [source.get("chunk", "") for source in sources if isinstance(source, dict)]
        
        return {
            "answer": answer,
            "contexts": contexts,
            "raw": body,
        }


def build_rag_client(config: dict) -> BaseRagClient:
    mode = config["run"]["mode"]
    rag_cfg = config["rag_app"]
    
    if mode == "local":
        return LocalRagClient(rag_cfg)
    elif mode == "lambda":
        return LambdaRagClient(rag_cfg)
    else:
        raise ValueError(f"Unknown run.mode: {mode}")

