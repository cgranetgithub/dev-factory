"""
DevFactory configuration.

All settings are loaded from environment variables or a `.env` file
via pydantic-settings. Copy `.env.example` to `.env` and fill in the values.

Environment variables are prefixed where noted (e.g. DEVFACTORY_POLL_INTERVAL).
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings resolved from environment variables / .env file.

    GitHub:
        GITHUB_TOKEN      Personal access token with repo + pull_request scopes.
        GITHUB_USERNAME   Your GitHub username (used for git author metadata).

    Ollama:
        OLLAMA_BASE_URL   Base URL of the Ollama server (default: localhost:11434).

    DevFactory:
        DEVFACTORY_POLL_INTERVAL   Seconds between GitHub issue polls (default: 60).
        DEVFACTORY_DB_PATH         Path to the SQLite knowledge-base file.
        DEVFACTORY_WORKSPACE       Directory where repositories are cloned.
        DEVFACTORY_MAX_VERIFICATION_RETRIES  Max developer→verification loop iterations per issue.
        DEVFACTORY_LOG_LEVEL       Logging verbosity (DEBUG / INFO / WARNING).
        DEVFACTORY_DEV_BACKEND     How the developer role writes code:
                                     "ollama"   — single-shot LLM call, full-file
                                                  rewrites parsed from the response
                                                  (default, self-contained);
                                     "opencode" — delegate to the OpenCode CLI, an
                                                  agentic tool loop that edits files
                                                  in place against a local Ollama model.

    OpenCode (only used when DEVFACTORY_DEV_BACKEND=opencode):
        OPENCODE_BIN        Path to the opencode binary (default: ~/.opencode/bin/opencode).
        OPENCODE_TIMEOUT_S  Seconds before an opencode run is killed (default: 1800).

    Docker:
        DOCKER_TEST_IMAGE   Name of the pre-built test image (devfactory-test:latest).
        OLLAMA_TIMEOUT_S    Seconds before an Ollama API call times out (default: 300).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GitHub
    github_token: str = ""
    github_username: str = ""

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout_s: int = Field(default=300, alias="OLLAMA_TIMEOUT_S")

    # DevFactory
    poll_interval: int = Field(default=60, alias="DEVFACTORY_POLL_INTERVAL")
    db_path: Path = Field(default=Path("./devfactory.db"), alias="DEVFACTORY_DB_PATH")
    workspace: Path = Field(default=Path("/tmp/devfactory"), alias="DEVFACTORY_WORKSPACE")
    max_verification_retries: int = Field(default=3, alias="DEVFACTORY_MAX_VERIFICATION_RETRIES")
    log_level: str = Field(default="INFO", alias="DEVFACTORY_LOG_LEVEL")

    # Developer backend: "ollama" (single-shot, default) or "opencode" (agentic CLI).
    dev_backend: str = Field(default="ollama", alias="DEVFACTORY_DEV_BACKEND")

    # OpenCode CLI (only used when dev_backend == "opencode"). The binary lives in
    # the user's home by default (installed via the standalone installer). Runs can
    # be long — the agentic loop makes many model calls — hence a wide timeout.
    opencode_bin: str = Field(
        default_factory=lambda: str(Path.home() / ".opencode" / "bin" / "opencode"),
        alias="OPENCODE_BIN",
    )
    opencode_timeout_s: int = Field(default=1800, alias="OPENCODE_TIMEOUT_S")

    # Docker
    docker_test_image: str = Field(default="devfactory-test:latest", alias="DOCKER_TEST_IMAGE")


# Singleton — import this everywhere
settings = Settings()
