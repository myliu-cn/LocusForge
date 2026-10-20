from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("LOCUS_DATA_DIR", "data")).resolve()
    )
    blender: str = field(default_factory=lambda: os.environ.get("LOCUS_BLENDER", "blender"))
    api_token: str = field(default_factory=lambda: os.environ.get("LOCUS_API_TOKEN", ""))
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            origin.strip().rstrip("/")
            for origin in os.environ.get(
                "LOCUS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if origin.strip()
        )
    )
    trusted_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            host.strip()
            for host in os.environ.get(
                "LOCUS_TRUSTED_HOSTS", "localhost,127.0.0.1,[::1],testserver"
            ).split(",")
            if host.strip()
        )
    )
    embedded_worker: bool = field(
        default_factory=lambda: os.environ.get("LOCUS_EMBEDDED_WORKER", "true").lower() == "true"
    )
    render_threads: int = field(
        default_factory=lambda: int(os.environ.get("LOCUS_RENDER_THREADS", "4"))
    )
    max_asset_mb: int = field(
        default_factory=lambda: int(os.environ.get("LOCUS_MAX_ASSET_MB", "1024"))
    )
    job_timeout_seconds: int = field(
        default_factory=lambda: int(os.environ.get("LOCUS_JOB_TIMEOUT_SECONDS", "21600"))
    )
    max_queued_jobs: int = 100
    llm_endpoint: str = field(
        default_factory=lambda: os.environ.get(
            "LOCUS_LLM_ENDPOINT", "https://www.dmxapi.cn/v1/chat/completions"
        )
    )
    llm_model: str = field(
        default_factory=lambda: os.environ.get("LOCUS_LLM_MODEL", "gpt-5.4-mini")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.environ.get("LOCUS_LLM_API_KEY", "").strip(), repr=False
    )
    llm_key_file: Path = field(
        default_factory=lambda: Path(os.environ.get("LOCUS_LLM_KEY_FILE", "api.key")).resolve()
    )
    llm_timeout_seconds: int = field(
        default_factory=lambda: int(os.environ.get("LOCUS_LLM_TIMEOUT_SECONDS", "45"))
    )
    llm_daily_limit: int = field(
        default_factory=lambda: int(os.environ.get("LOCUS_LLM_DAILY_LIMIT", "20"))
    )
    static_dir: Path = PACKAGE_DIR / "static"

    def __post_init__(self) -> None:
        if self.api_token and (len(self.api_token) < 32 or not self.api_token.isascii()):
            raise ValueError("LOCUS_API_TOKEN must contain at least 32 ASCII characters")
        if not 1 <= self.render_threads <= 64:
            raise ValueError("LOCUS_RENDER_THREADS must be between 1 and 64")
        if not 1 <= self.max_asset_mb <= 8192:
            raise ValueError("LOCUS_MAX_ASSET_MB must be between 1 and 8192")
        if not 30 <= self.job_timeout_seconds <= 604800:
            raise ValueError("LOCUS_JOB_TIMEOUT_SECONDS must be between 30 and 604800")
        endpoint = urlsplit(self.llm_endpoint)
        if (
            not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or endpoint.scheme not in ("http", "https")
            or (
                endpoint.scheme == "http"
                and endpoint.hostname not in ("localhost", "127.0.0.1", "::1")
            )
        ):
            raise ValueError(
                "LOCUS_LLM_ENDPOINT must be a credential-free HTTPS URL (HTTP only for loopback)"
            )
        if not self.llm_model.strip() or len(self.llm_model) > 100:
            raise ValueError("LOCUS_LLM_MODEL must be a nonempty model identifier")
        if not 5 <= self.llm_timeout_seconds <= 120 or not 0 <= self.llm_daily_limit <= 1000:
            raise ValueError("LLM timeout must be 5-120 seconds; daily call limit must be 0-1000")

    @property
    def blender_executable(self) -> str | None:
        return shutil.which(self.blender)

    @property
    def database(self) -> Path:
        return self.data_dir / "locusforge.sqlite3"

    @property
    def llm_configured(self) -> bool:
        return self.llm_daily_limit > 0 and bool(self.llm_api_key or self.llm_key_file.is_file())

    def llm_credential(self) -> str:
        value = self.llm_api_key
        try:
            if not value and self.llm_key_file.is_file():
                if self.llm_key_file.stat().st_size > 4096:
                    raise ValueError(
                        "The configured LLM key file exceeds the credential size limit"
                    )
                value = self.llm_key_file.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError):
            raise ValueError(
                "Cannot read the configured LLM key file; check permissions and UTF-8 encoding"
            ) from None
        if (
            not value
            or len(value) > 4096
            or not value.isascii()
            or any(character.isspace() for character in value)
        ):
            raise ValueError(
                "Set LOCUS_LLM_API_KEY or a valid LOCUS_LLM_KEY_FILE before cloud parsing"
            )
        return value

    def initialize(self) -> None:
        for directory in ("assets", "jobs", "incoming"):
            (self.data_dir / directory).mkdir(parents=True, exist_ok=True)
