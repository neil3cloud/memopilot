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


async def analyze_image(file_path: str | Path, allow_cloud: bool = False) -> ImageAnalysisResult:
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
        cloud_result = await _analyze_image_cloud(candidate)
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


async def _analyze_image_cloud(file_path: Path) -> ImageAnalysisResult:
    message = "Cloud vision fallback is not configured."
    return ImageAnalysisResult(
        source="cloud",
        ocr_text=extract_ocr_text(file_path) or "",
        error=message,
        error_messages=[message],
    )


def _extract_tagged_lines(text: str, keywords: tuple[str, ...]) -> list[str]:
    findings: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" -?	")
        if line and any(keyword in line.lower() for keyword in keywords):
            findings.append(line)
    return findings
