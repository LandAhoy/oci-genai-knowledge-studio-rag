import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import oci
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse


REGION = os.environ["OCI_REGION"]
COMPARTMENT_ID = os.environ["OCI_COMPARTMENT_ID"]

# Keep chat and vision separate. Command A supports tools; Command A Vision does not.
TOOL_MODEL = os.environ.get("OCI_TOOL_MODEL", "cohere.command-a-03-2025")
VISION_MODEL = os.environ.get(
    "OCI_VISION_MODEL",
    os.environ.get("OCI_CHAT_MODEL", "cohere.command-a-vision"),
)
EMBED_MODEL = os.environ["OCI_EMBED_MODEL"]
EMBED_DIMENSIONS = int(os.environ.get("OCI_EMBED_DIMENSIONS", "1536"))
DEFAULT_MAX_TOKENS = int(os.environ.get("OCI_MAX_OUTPUT_TOKENS", "4000"))
OCI_ON_DEMAND_MAX_TOKENS = 4000

signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
client = oci.generative_ai_inference.GenerativeAiInferenceClient(
    config={"region": REGION},
    signer=signer,
    service_endpoint=(
        f"https://inference.generativeai.{REGION}.oci.oraclecloud.com"
    ),
)
models = oci.generative_ai_inference.models

app = FastAPI(title="OCI Generative AI Adapter", version="2.0.0")


def text_content(text: str):
    return models.CohereTextContentV2(type="TEXT", text=text)


def normalize_content(content: Any) -> List[Dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content)}]


def object_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def cohere_parts(message: Dict[str, Any], allow_images: bool) -> list:
    role = str(message.get("role", "user")).lower()
    output = []
    for part in normalize_content(message.get("content")):
        if not isinstance(part, dict):
            output.append(text_content(str(part)))
            continue
        part_type = str(part.get("type", "text")).lower()
        if part_type == "text":
            output.append(text_content(str(part.get("text", ""))))
        elif part_type == "image_url" and role == "user":
            if not allow_images:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Model {TOOL_MODEL} is text/tool capable but not a VLM. "
                        f"Use {VISION_MODEL} for image requests."
                    ),
                )
            image_value = part.get("image_url", {})
            if isinstance(image_value, dict):
                image_url = str(image_value.get("url", ""))
                detail = str(image_value.get("detail", "AUTO")).upper()
            else:
                image_url = str(image_value)
                detail = "AUTO"
            if image_url:
                output.append(
                    models.CohereImageContentV2(
                        type="IMAGE_URL",
                        image_url=models.CohereImageUrlV2(
                            url=image_url,
                            detail=detail,
                        ),
                    )
                )
    return output


def openai_tool_calls_to_oci(tool_calls: Any) -> list:
    output = []
    for call in tool_calls or []:
        function = object_value(call, "function", {})
        name = object_value(function, "name", "")
        arguments = object_value(function, "arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        output.append(
            models.CohereToolCallV2(
                id=str(object_value(call, "id", "")),
                type="FUNCTION",
                function={"name": str(name), "arguments": arguments},
            )
        )
    return output


def convert_message(message: Dict[str, Any], allow_images: bool):
    role = str(message.get("role", "user")).lower()
    parts = cohere_parts(message, allow_images)

    if role == "system":
        return models.CohereSystemMessageV2(
            role="SYSTEM",
            content=parts or [text_content("")],
        )
    if role == "assistant":
        return models.CohereAssistantMessageV2(
            role="ASSISTANT",
            content=parts,
            tool_calls=openai_tool_calls_to_oci(message.get("tool_calls")),
        )
    if role == "tool":
        return models.CohereToolMessageV2(
            role="TOOL",
            content=parts or [text_content("")],
            tool_call_id=str(message.get("tool_call_id", "")),
        )
    if role != "user":
        raise HTTPException(status_code=400, detail=f"Unsupported role: {role}")
    return models.CohereUserMessageV2(
        role="USER",
        content=parts or [text_content("")],
    )


def convert_tools(raw_tools: Any) -> list:
    output = []
    for item in raw_tools or []:
        if not isinstance(item, dict) or item.get("type") != "function":
            raise HTTPException(
                status_code=400,
                detail="Only OpenAI function tools are supported.",
            )
        function = item.get("function") or {}
        name = str(function.get("name", ""))
        parameters = function.get("parameters") or {
            "type": "object",
            "properties": {},
        }
        if not name or not isinstance(parameters, dict):
            raise HTTPException(
                status_code=400,
                detail="Each tool requires a name and an object JSON schema.",
            )
        output.append(
            models.CohereToolV2(
                type="FUNCTION",
                function=models.Function(
                    name=name,
                    description=str(function.get("description", "")),
                    parameters=parameters,
                ),
            )
        )
    return output


def tool_choice(raw_choice: Any, tools: list) -> tuple[Optional[str], list]:
    if raw_choice in (None, "auto"):
        # OCI Cohere V2 represents automatic selection by omitting tools_choice.
        return None, tools
    if raw_choice == "none":
        return "NONE", tools
    if raw_choice == "required":
        return "REQUIRED", tools
    if isinstance(raw_choice, dict):
        function = raw_choice.get("function") or {}
        selected_name = function.get("name")
        selected = [
            tool
            for tool in tools
            if object_value(object_value(tool, "function", {}), "name")
            == selected_name
        ]
        if not selected:
            raise HTTPException(
                status_code=400,
                detail=f"Requested tool is not defined: {selected_name}",
            )
        return "REQUIRED", selected
    raise HTTPException(status_code=400, detail="Unsupported tool_choice value.")


def extract_response_text(chat_response: Any) -> str:
    message = getattr(chat_response, "message", None)
    content = getattr(message, "content", None) or []
    return "".join(
        str(getattr(item, "text"))
        for item in content
        if getattr(item, "text", None)
    )


def extract_tool_calls(chat_response: Any) -> list:
    message = getattr(chat_response, "message", None)
    output = []
    for index, call in enumerate(getattr(message, "tool_calls", None) or []):
        function = object_value(call, "function", {})
        arguments = object_value(function, "arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        output.append(
            {
                "index": index,
                "id": str(object_value(call, "id", "")),
                "type": "function",
                "function": {
                    "name": str(object_value(function, "name", "")),
                    "arguments": arguments,
                },
            }
        )
    return output


def finish_reason(value: str, has_tool_calls: bool = False) -> str:
    if has_tool_calls:
        return "tool_calls"
    return {
        "COMPLETE": "stop",
        "STOP_SEQUENCE": "stop",
        "MAX_TOKENS": "length",
        "TOOL_CALL": "tool_calls",
        "ERROR": "stop",
    }.get(str(value).upper(), "stop")


def usage_value(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None) if usage is not None else None
        if isinstance(value, int):
            return value
    return 0


def bounded_max_tokens(body: Dict[str, Any]) -> int:
    requested = body.get("max_completion_tokens")
    if requested is None:
        requested = body.get("max_tokens")
    if requested in (None, 0, "0"):
        requested = DEFAULT_MAX_TOKENS
    try:
        value = int(requested)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="max_tokens must be an integer.")
    return max(1, min(value, OCI_ON_DEMAND_MAX_TOKENS))


def optional_float(body: Dict[str, Any], name: str) -> Optional[float]:
    value = body.get(name)
    return None if value is None else float(value)


def requested_model(body: Dict[str, Any]) -> str:
    name = str(body.get("model") or TOOL_MODEL)
    if name not in {TOOL_MODEL, VISION_MODEL}:
        raise HTTPException(status_code=404, detail=f"Unknown chat model: {name}")
    if body.get("tools") and name != TOOL_MODEL:
        raise HTTPException(
            status_code=400,
            detail=f"Tool calling requires model {TOOL_MODEL}; {name} is VLM-only.",
        )
    return name


def oci_service_error(error: oci.exceptions.ServiceError):
    raise HTTPException(
        status_code=error.status or 500,
        detail={
            "oci_code": error.code,
            "message": error.message,
            "request_id": error.request_id,
        },
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "oci-generative-ai-adapter",
        "version": "2.0.0",
        "region": REGION,
        "tool_model": TOOL_MODEL,
        "vision_model": VISION_MODEL,
        "embedding_model": EMBED_MODEL,
    }


@app.get("/v1/models")
def list_models():
    created = int(time.time())
    specs = [
        (TOOL_MODEL, 256000, ["chat", "tools"]),
        (VISION_MODEL, 128000, ["chat", "vision"]),
        (EMBED_MODEL, None, ["embedding"]),
    ]
    return {
        "object": "list",
        "data": [
            {
                "id": name,
                "object": "model",
                "created": created,
                "owned_by": "oracle-cloud-infrastructure",
                "max_tokens": limit,
                "capabilities": capabilities,
            }
            for name, limit, capabilities in specs
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(body: Dict[str, Any]):
    raw_messages = body.get("messages") or []
    if not raw_messages:
        raise HTTPException(status_code=400, detail="The messages array cannot be empty.")

    model_name = requested_model(body)
    converted_messages = [
        convert_message(message, allow_images=(model_name == VISION_MODEL))
        for message in raw_messages
    ]
    converted_tools = convert_tools(body.get("tools"))
    choice, converted_tools = tool_choice(body.get("tool_choice"), converted_tools)

    stop = body.get("stop")
    if isinstance(stop, str):
        stop = [stop]
    elif stop is not None and not isinstance(stop, list):
        raise HTTPException(status_code=400, detail="stop must be a string or list.")

    request_options = {
        "api_format": "COHEREV2",
        "messages": converted_messages,
        # The OCI SDK's token stream is not OpenAI SSE. We make one OCI call and
        # serialize its result into standards-compliant OpenAI chunks below.
        "is_stream": False,
        "max_tokens": bounded_max_tokens(body),
        "temperature": optional_float(body, "temperature"),
        "top_p": optional_float(body, "top_p"),
        "frequency_penalty": optional_float(body, "frequency_penalty"),
        "presence_penalty": optional_float(body, "presence_penalty"),
        "stop_sequences": stop,
        "safety_mode": "CONTEXTUAL",
    }
    if converted_tools:
        request_options["tools"] = converted_tools
    if choice is not None:
        request_options["tools_choice"] = choice

    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    try:
        response = client.chat(
            chat_details=models.ChatDetails(
                compartment_id=COMPARTMENT_ID,
                serving_mode=models.OnDemandServingMode(
                    serving_type="ON_DEMAND",
                    model_id=model_name,
                ),
                chat_request=models.CohereChatRequestV2(**request_options),
            )
        )
    except oci.exceptions.ServiceError as error:
        oci_service_error(error)

    result = response.data.chat_response
    answer = extract_response_text(result)
    response_tool_calls = extract_tool_calls(result)
    stop_reason = finish_reason(
        getattr(result, "finish_reason", "COMPLETE"),
        bool(response_tool_calls),
    )
    usage = getattr(result, "usage", None)
    prompt_tokens = usage_value(usage, "prompt_tokens", "input_tokens")
    completion_tokens = usage_value(usage, "completion_tokens", "output_tokens")
    created = int(time.time())
    message = {"role": "assistant", "content": answer or None}
    if response_tool_calls:
        message["tool_calls"] = [
            {key: value for key, value in call.items() if key != "index"}
            for call in response_tool_calls
        ]

    completion = {
        "id": request_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [{"index": 0, "message": message, "finish_reason": stop_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    if not body.get("stream", False):
        return completion

    def event_stream():
        initial_delta: Dict[str, Any] = {"role": "assistant"}
        if response_tool_calls:
            initial_delta["tool_calls"] = response_tool_calls
        else:
            initial_delta["content"] = answer
        yield "data: " + json.dumps(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_name,
                "choices": [
                    {"index": 0, "delta": initial_delta, "finish_reason": None}
                ],
            },
            ensure_ascii=False,
        ) + "\n\n"
        yield "data: " + json.dumps(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_name,
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": stop_reason}
                ],
                "usage": completion["usage"],
            }
        ) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/v1/embeddings")
def embeddings(body: Dict[str, Any]):
    requested_input = body.get("input")
    if requested_input is None:
        raise HTTPException(status_code=400, detail="The input field is required.")
    if isinstance(requested_input, str):
        inputs = [requested_input]
    elif isinstance(requested_input, list):
        inputs = [str(value) for value in requested_input]
    else:
        raise HTTPException(
            status_code=400,
            detail="Input must be a string or an array of strings.",
        )
    if not inputs:
        raise HTTPException(status_code=400, detail="The input array cannot be empty.")

    requested_type = str(body.get("input_type", "")).upper()
    if requested_type not in {
        "SEARCH_QUERY",
        "SEARCH_DOCUMENT",
        "CLASSIFICATION",
        "CLUSTERING",
    }:
        requested_type = "SEARCH_QUERY" if len(inputs) == 1 else "SEARCH_DOCUMENT"
    dimensions = int(body.get("dimensions") or EMBED_DIMENSIONS)

    try:
        response = client.embed_text(
            embed_text_details=models.EmbedTextDetails(
                compartment_id=COMPARTMENT_ID,
                serving_mode=models.OnDemandServingMode(
                    serving_type="ON_DEMAND",
                    model_id=EMBED_MODEL,
                ),
                inputs=inputs,
                input_type=requested_type,
                truncate="END",
                embedding_types=["float"],
                output_dimensions=dimensions,
                is_echo=False,
            )
        )
    except oci.exceptions.ServiceError as error:
        oci_service_error(error)

    vectors = response.data.embeddings
    if vectors is None:
        by_type = response.data.embeddings_by_type or {}
        vectors = by_type.get("float") or by_type.get("FLOAT")
    if vectors is None:
        raise HTTPException(status_code=502, detail="OCI returned no float embeddings.")

    return {
        "object": "list",
        "model": EMBED_MODEL,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
