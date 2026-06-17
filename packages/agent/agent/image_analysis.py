"""Image and screenshot analysis for MemoPilot."""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config_loader import load_provider_config

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_OLLAMA_URL = "http://localhost:11434/api/generate"


@dataclass
class ImageAnalysisResult:
    """Result of image analysis."""

    description: str = ""
    ocr_text: str = ""
    ui_elements: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    source: str = "local"
    trust_level: int = 2
    memory_status: str = "evidence_only"
    error: str | None = None


def is_supported_image(file_path: str | Path) -> bool:
    """Check if file is a supported image format."""
    return Path(file_path).suffix.lower() in SUPPORTED_FORMATS


def extract_ocr_text(file_path: str | Path) -> str | None:
    """Extract text from an image using Pillow + pytesseract."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning("Pillow or pytesseract is unavailable; OCR skipped.")
        return None

    try:
        with Image.open(str(file_path)) as image:
            text = pytesseract.image_to_string(image).strip()
        return text or None
    except Exception as exc:
        logger.warning("OCR failed for %s: %s", file_path, exc)
        return None


def check_ollama_llava() -> bool:
    """Check whether a local Ollama LLaVA model is available."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and "llava" in result.stdout.lower()


async def analyze_image(
    file_path: str | Path,
    allow_cloud: bool = False,
    workspace_root: str | None = None,
) -> ImageAnalysisResult:
    """Analyze an image with local vision first, then OCR, then cloud if allowed."""
    candidate = Path(file_path)
    if not is_supported_image(candidate):
        message = f"Unsupported image format: {candidate.suffix}"
        return ImageAnalysisResult(source="unsupported", error=message, error_messages=[message])
    if not candidate.exists():
        message = f"Image not found: {candidate}"
        return ImageAnalysisResult(source="missing", error=message, error_messages=[message])

    local_result = await _analyze_image_local(candidate)
    if local_result.error is None:
        return local_result

    ocr_text = extract_ocr_text(candidate)
    if ocr_text:
        return ImageAnalysisResult(
            description="Text extracted via OCR only.",
            ocr_text=ocr_text,
            source="ocr_only",
            error_messages=list(local_result.error_messages),
        )

    if allow_cloud:
        cloud_result = await _analyze_image_cloud(candidate, workspace_root=workspace_root)
        if cloud_result.error is None:
            return cloud_result
        return ImageAnalysisResult(
            description=cloud_result.description,
            ocr_text=cloud_result.ocr_text,
            ui_elements=cloud_result.ui_elements,
            error_messages=[*local_result.error_messages, *cloud_result.error_messages],
            source=cloud_result.source,
            error=cloud_result.error,
        )

    fallback_error = local_result.error or "No local image analysis path succeeded."
    return ImageAnalysisResult(
        source="unavailable",
        error=fallback_error,
        error_messages=[*local_result.error_messages, "Cloud analysis not permitted."],
    )


async def _analyze_image_local(file_path: Path) -> ImageAnalysisResult:
    if not check_ollama_llava():
        message = "LLaVA not available locally. Install with: ollama pull llava"
        return ImageAnalysisResult(source="local", error=message, error_messages=[message])

    try:
        payload = json.dumps(
            {
                "model": "llava",
                "prompt": (
                    "Describe this image. Identify UI elements, error messages, and visible text. "
                    "Return a concise plain-language summary."
                ),
                "images": [base64.b64encode(file_path.read_bytes()).decode("utf-8")],
                "stream": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            _OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        message = f"Local analysis failed: {exc}"
        return ImageAnalysisResult(source="local", error=message, error_messages=[message])

    description = str(data.get("response", "")).strip()
    ocr_text = extract_ocr_text(file_path) or ""
    return ImageAnalysisResult(
        description=description,
        ocr_text=ocr_text,
        ui_elements=_extract_tagged_lines(
            description, ("button", "menu", "dialog", "input", "field", "tab", "panel")
        ),
        error_messages=_extract_tagged_lines(
            description, ("error", "warning", "failed", "exception")
        ),
        source="local",
    )


async def _analyze_image_cloud(
    file_path: Path,
    workspace_root: str | None = None,
) -> ImageAnalysisResult:
    root = workspace_root or _infer_workspace_root(file_path)
    provider_config = load_provider_config(root)

    configured_order = provider_config.get("fallback_order", [])
    if not isinstance(configured_order, list):
        configured_order = []
    cloud_order = [p for p in configured_order if p in {"openai", "anthropic"}]
    if not cloud_order:
        cloud_order = ["openai", "anthropic"]

    image_base64 = base64.b64encode(file_path.read_bytes()).decode("utf-8")
    media_type = _guess_media_type(file_path)
    errors: list[str] = []

    for provider in cloud_order:
        try:
            if provider == "openai":
                key = str(provider_config.get("openai_api_key") or "").strip()
                if not key:
                    errors.append("OpenAI cloud vision skipped: openai_api_key not configured.")
                    continue
                model = str(provider_config.get("openai_model") or "gpt-4o-mini")
                raw = await _call_openai_vision(
                    api_key=key,
                    model=model,
                    image_base64=image_base64,
                    media_type=media_type,
                )
                parsed = _parse_cloud_vision_json(raw)
                ocr_text = parsed.get("ocr_text") or extract_ocr_text(file_path) or ""
                description = str(parsed.get("description") or "").strip()
                if not description:
                    description = raw.strip() or "Cloud image analysis completed."
                return ImageAnalysisResult(
                    description=description,
                    ocr_text=ocr_text,
                    ui_elements=_normalize_list(parsed.get("ui_elements")),
                    error_messages=_normalize_list(parsed.get("error_messages")),
                    source=f"cloud:{provider}",
                    trust_level=3,
                    memory_status="evidence_only",
                )

            if provider == "anthropic":
                key = str(provider_config.get("anthropic_api_key") or "").strip()
                if not key:
                    errors.append("Anthropic cloud vision skipped: anthropic_api_key not configured.")
                    continue
                model = str(provider_config.get("anthropic_model") or "claude-haiku-4-5")
                raw = await _call_anthropic_vision(
                    api_key=key,
                    model=model,
                    image_base64=image_base64,
                    media_type=media_type,
                )
                parsed = _parse_cloud_vision_json(raw)
                ocr_text = parsed.get("ocr_text") or extract_ocr_text(file_path) or ""
                description = str(parsed.get("description") or "").strip()
                if not description:
                    description = raw.strip() or "Cloud image analysis completed."
                return ImageAnalysisResult(
                    description=description,
                    ocr_text=ocr_text,
                    ui_elements=_normalize_list(parsed.get("ui_elements")),
                    error_messages=_normalize_list(parsed.get("error_messages")),
                    source=f"cloud:{provider}",
                    trust_level=3,
                    memory_status="evidence_only",
                )
        except Exception as exc:
            errors.append(f"{provider} cloud vision failed: {exc}")

    message = "Cloud vision fallback is not configured or failed."
    return ImageAnalysisResult(
        source="cloud",
        ocr_text=extract_ocr_text(file_path) or "",
        error=message,
        error_messages=errors or [message],
    )


def _infer_workspace_root(file_path: Path) -> str:
    for parent in [file_path.parent, *file_path.parents]:
        if (parent / ".memopilot").exists():
            return str(parent)
    return str(file_path.parent)


def _guess_media_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".bmp":
        return "image/bmp"
    return "image/png"


def _vision_prompt() -> str:
    return (
        "Analyze this image for software engineering workflow context. "
        "Return strict JSON with keys: description (string), ui_elements (string[]), "
        "error_messages (string[]), ocr_text (string). Keep it concise and factual."
    )


async def _call_openai_vision(
    *,
    api_key: str,
    model: str,
    image_base64: str,
    media_type: str,
) -> str:
    payload = {
        "model": model,
        "max_tokens": 400,
        "messages": [
            {
                "role": "system",
                "content": "You are an image analysis assistant. Return strict JSON only.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _vision_prompt()},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_base64}"},
                    },
                ],
            },
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
    data = response.json()
    return str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))


async def _call_anthropic_vision(
    *,
    api_key: str,
    model: str,
    image_base64: str,
    media_type: str,
) -> str:
    payload = {
        "model": model,
        "max_tokens": 400,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64,
                        },
                    },
                    {"type": "text", "text": _vision_prompt()},
                ],
            }
        ],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
    data = response.json()
    chunks = data.get("content", [])
    text_parts = [str(chunk.get("text", "")) for chunk in chunks if chunk.get("type") == "text"]
    return "\n".join(part for part in text_parts if part).strip()


def _parse_cloud_vision_json(raw: str) -> dict:
    text = raw.strip()
    if not text:
        return {}
    for candidate in (text, _extract_json_block(text)):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    return {}


def _extract_json_block(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return ""


def _normalize_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                normalized.append(stripped)
    return normalized


def _extract_tagged_lines(text: str, keywords: tuple[str, ...]) -> list[str]:
    findings: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" -?	")
        if line and any(keyword in line.lower() for keyword in keywords):
            findings.append(line)
    return findings
