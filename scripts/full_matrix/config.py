"""Configuration for the full-matrix orchestration run.

This module only assembles paths, names, and process-local credentials.  It
deliberately keeps secrets out of ``public_dict()`` and out of run manifests.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


RUN_ID = os.environ.get("FULL_MATRIX_RUN_ID", "20261111-impl01")
DEFAULT_PORT = int(os.environ.get("FULL_MATRIX_DEFAULT_PORT", "8121"))
REAL_SERVER_PORT = 8000
SOURCE_ROOT = Path(os.environ.get("FULL_MATRIX_SOURCE_ROOT", str(Path(__file__).resolve().parents[2]))).resolve()
SANDBOX_ROOT = Path(
    os.environ.get("FULL_MATRIX_SANDBOX_ROOT", str(Path(tempfile.gettempdir()) / "docnotice-e2e" / RUN_ID))
).resolve()
_EVIDENCE_ROOT_ENV = os.environ.get("FULL_MATRIX_EVIDENCE_ROOT", "").strip()
EVIDENCE_ROOT = Path(_EVIDENCE_ROOT_ENV).resolve() if _EVIDENCE_ROOT_ENV else SOURCE_ROOT / f"_agent_full_matrix_{RUN_ID}"
TASK_SPACE_NAME = os.environ.get("FULL_MATRIX_TASK_SPACE", f"ego-full-matrix-{RUN_ID}")

# _paths.py derives all legacy E2E paths from this variable.
FMS_TMP_DIR = SANDBOX_ROOT
SANDBOX_CODE = SANDBOX_ROOT / "code"
SANDBOX_DATA = SANDBOX_ROOT / "data"
SANDBOX_E2E_FILES = SANDBOX_ROOT / "e2e_files"
SANDBOX_RUNTIME = SANDBOX_ROOT / "runtime"
SANDBOX_LOGS = SANDBOX_ROOT / "logs"
SANDBOX_REPORT = SANDBOX_ROOT / "report"
EVIDENCE_EGO = EVIDENCE_ROOT / "ego"
EVIDENCE_PYTEST = EVIDENCE_ROOT / "pytest"
EVIDENCE_E2E = EVIDENCE_ROOT / "e2e"
EVIDENCE_ROUNDS = EVIDENCE_EGO / "rounds"
EVIDENCE_RESULTS = EVIDENCE_EGO / "results"

HOST_ORIGINS = {
    "admin": f"http://admin.localhost:{DEFAULT_PORT}",
    "user_a": f"http://user-a.localhost:{DEFAULT_PORT}",
    "user_b": f"http://user-b.localhost:{DEFAULT_PORT}",
    "target": f"http://target.localhost:{DEFAULT_PORT}",
    "outside": f"http://outside.localhost:{DEFAULT_PORT}",
    "anon": f"http://anon.localhost:{DEFAULT_PORT}",
}

DEMO_DEFAULT_PASSWORD = "Demo@123456"
DEMO_ACCOUNTS = {
    "admin": "admin_demo",
    "admin2": "admin2_demo",
    "user_a": "user_rnd_1",
    "user_fin": "user_fin_1",
    "user_hr": "user_hr_1",
}


def _env_text(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _random_initial_password() -> str:
    # Meets the application's >=8 chars + alpha + digit rule; never written to JSON.
    return f"Fm{secrets.token_urlsafe(18)}9a"


@dataclass(frozen=True)
class MatrixConfig:
    """All process-local configuration for one full-matrix run."""

    run_id: str = RUN_ID
    source_root: Path = SOURCE_ROOT
    sandbox_root: Path = SANDBOX_ROOT
    evidence_root: Path = EVIDENCE_ROOT
    task_space: str = TASK_SPACE_NAME
    port: int = DEFAULT_PORT
    real_port: int = REAL_SERVER_PORT

    secret_key: str = ""
    initial_admin_username: str = f"matrix_initial_{RUN_ID}"
    initial_admin_password: str = ""
    admin_username: str = DEMO_ACCOUNTS["admin"]
    admin_password: str = ""
    user_username: str = DEMO_ACCOUNTS["user_a"]
    user_password: str = ""

    @classmethod
    def from_env(cls) -> "MatrixConfig":
        port_text = os.environ.get("FULL_MATRIX_PORT", str(DEFAULT_PORT))
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"FULL_MATRIX_PORT must be an integer: {port_text!r}") from exc
        if not 1 <= port <= 65535:
            raise ValueError("FULL_MATRIX_PORT must be between 1 and 65535")
        return cls(
            run_id=RUN_ID,
            source_root=SOURCE_ROOT,
            sandbox_root=SANDBOX_ROOT,
            evidence_root=EVIDENCE_ROOT,
            task_space=TASK_SPACE_NAME,
            port=port,
            secret_key=_env_text("FULL_MATRIX_SECRET_KEY", secrets.token_urlsafe(48)),
            initial_admin_password=_env_text(
                "FULL_MATRIX_INITIAL_ADMIN_PASSWORD", _random_initial_password()
            ),
            admin_password=_env_text("E2E_ADMIN_PWD", DEMO_DEFAULT_PASSWORD),
            user_password=_env_text("E2E_USER_PWD", DEMO_DEFAULT_PASSWORD),
        )

    @property
    def source_python(self) -> Path:
        return self.source_root / ".venv" / "Scripts" / "python.exe"

    @property
    def sandbox_python(self) -> Path:
        # The copied tree intentionally excludes .venv.  Use the source venv
        # but execute code from the sandbox working directory.
        return self.source_python

    @property
    def code_dir(self) -> Path:
        return self.sandbox_root / "code"

    @property
    def data_dir(self) -> Path:
        return self.sandbox_root / "data"

    @property
    def e2e_files_dir(self) -> Path:
        return self.sandbox_root / "e2e_files"

    @property
    def fms_tmp_dir(self) -> Path:
        return self.sandbox_root

    @property
    def database_path(self) -> Path:
        return self.data_dir / "database" / "app.db"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/health"

    @property
    def origin(self) -> str:
        return f"http://admin.localhost:{self.port}"

    def host_origin(self, host: str) -> str:
        return f"http://{host}:{self.port}"

    def env(self, *, include_test_accounts: bool = True) -> dict[str, str]:
        """Return the complete child-process environment.

        ``DATABASE_URL`` is deliberately empty so the application derives the
        database path from DATA_DIR.  Secrets remain in this mapping only; do
        not serialize the mapping to evidence.
        """
        values = {
            "APP_ENV": "test",
            "DATA_DIR": str(self.data_dir),
            "DATABASE_URL": "",
            "HOST": "127.0.0.1",
            "PORT": str(self.port),
            "SECRET_KEY": self.secret_key,
            "JWT_ALGORITHM": "HS256",
            "JWT_EXPIRE_MINUTES": "720",
            "COOKIE_SECURE": "false",
            "AUTH_PROVIDER": "local",
            "BACKUP_SCHEDULE": "",
            "BACKUP_KEEP_DAYS": "0",
            "BACKUP_KEEP_COUNT": "0",
            "BACKUP_PRE_KEEP_COUNT": "0",
            "LOGIN_RATE_MAX_ATTEMPTS": "5000",
            "INITIAL_ADMIN_USERNAME": self.initial_admin_username,
            "INITIAL_ADMIN_PASSWORD": self.initial_admin_password,
            "TRUST_REVERSE_PROXY_HEADERS": "false",
            "TRUSTED_HOSTS": "localhost,*.localhost,127.0.0.1,::1,testserver",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "FMS_TMP_DIR": str(self.fms_tmp_dir),
            "E2E_DATA_DIR": str(self.fms_tmp_dir / "fms_e2e_data"),
            "E2E_FILES_DIR": str(self.e2e_files_dir),
            "FULL_MATRIX_SANDBOX_ROOT": str(self.sandbox_root),
            "FULL_MATRIX_EVIDENCE_ROOT": str(self.evidence_root),
            "FULL_MATRIX_RUN_ID": self.run_id,
            "E2E_BASE_URL": self.base_url,
            "E2E_ADMIN": self.admin_username,
            "E2E_ADMIN_PWD": self.admin_password,
            "E2E_USER": self.user_username,
            "E2E_USER_PWD": self.user_password,
        }
        if not include_test_accounts:
            for key in ("E2E_ADMIN_PWD", "E2E_USER_PWD", "INITIAL_ADMIN_PASSWORD"):
                values.pop(key, None)
        return values

    def child_env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        merged = os.environ.copy()
        merged.update(self.env())
        if extra:
            merged.update({str(k): str(v) for k, v in extra.items()})
        return merged

    def public_dict(self) -> dict[str, Any]:
        """Metadata safe to write to run.json or a report."""
        return {
            "run_id": self.run_id,
            "source_root": str(self.source_root),
            "sandbox_root": str(self.sandbox_root),
            "evidence_root": str(self.evidence_root),
            "task_space": self.task_space,
            "port": self.port,
            "real_port": self.real_port,
            "base_url": self.base_url,
            "fms_tmp_dir": str(self.fms_tmp_dir),
            "data_dir": str(self.data_dir),
            "database_path": str(self.database_path),
            "e2e_files_dir": str(self.e2e_files_dir),
            "source_python": str(self.source_python),
            "hosts": HOST_ORIGINS,
            "credentials": {
                "admin": self.admin_username,
                "user": self.user_username,
                "demo_password_source": "E2E_ADMIN_PWD/E2E_USER_PWD or Demo@123456",
                "initial_admin_user": self.initial_admin_username,
            },
        }

    def sanitize(self, text: str) -> str:
        """Redact process-local secrets from any diagnostic string."""
        redacted = text
        for secret in (
            self.secret_key,
            self.initial_admin_password,
            self.admin_password,
            self.user_password,
        ):
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        for marker in ("Authorization:", "Set-Cookie:", "access_token=", "refresh_token="):
            if marker in redacted:
                redacted = redacted.replace(marker, f"{marker}[REDACTED]")
        return redacted

    def ensure_layout(self) -> None:
        """Create only sandbox and evidence directories; never touches real data."""
        for path in (
            self.sandbox_root,
            self.code_dir,
            self.data_dir / "database",
            self.data_dir / "documents",
            self.data_dir / "notices",
            self.data_dir / "uploads",
            self.data_dir / "recycle_bin",
            self.data_dir / "backups",
            self.data_dir / "logs",
            self.e2e_files_dir,
            self.sandbox_root / "runtime",
            self.sandbox_root / "report",
            self.evidence_root,
            self.evidence_root / "ego" / "rounds",
            self.evidence_root / "ego" / "results",
            self.evidence_root / "ego" / "screenshots",
            self.evidence_root / "ego" / "console",
            self.evidence_root / "ego" / "network",
            self.evidence_root / "ego" / "downloads",
            self.evidence_root / "pytest",
            self.evidence_root / "e2e",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict[str, Any]:
        return self.public_dict()


DEFAULT_CONFIG = MatrixConfig.from_env()

__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_PORT",
    "DEMO_ACCOUNTS",
    "DEMO_DEFAULT_PASSWORD",
    "EVIDENCE_E2E",
    "EVIDENCE_EGO",
    "EVIDENCE_PYTEST",
    "EVIDENCE_RESULTS",
    "EVIDENCE_ROOT",
    "EVIDENCE_ROUNDS",
    "FMS_TMP_DIR",
    "HOST_ORIGINS",
    "MatrixConfig",
    "REAL_SERVER_PORT",
    "RUN_ID",
    "SANDBOX_CODE",
    "SANDBOX_DATA",
    "SANDBOX_E2E_FILES",
    "SANDBOX_LOGS",
    "SANDBOX_REPORT",
    "SANDBOX_ROOT",
    "SANDBOX_RUNTIME",
    "SOURCE_ROOT",
    "TASK_SPACE_NAME",
]