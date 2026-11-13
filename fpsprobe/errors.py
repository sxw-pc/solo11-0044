"""Typed errors with stable machine-readable codes and exit codes."""

from __future__ import annotations


class FpsProbeError(Exception):
    """Base error for the application.

    Exit code conventions:
    0 - success
    1 - a completed inspection contains a reject classification
    2 - configuration or input problem
    3 - internal error
    """

    default_code = "INTERNAL_ERROR"
    exit_code = 3

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        if exit_code is not None:
            self.exit_code = exit_code

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


class ConfigError(FpsProbeError):
    default_code = "CONFIG_ERROR"
    exit_code = 2


class ConfigNotFound(ConfigError):
    default_code = "CONFIG_NOT_FOUND"


class ConfigParseError(ConfigError):
    default_code = "CONFIG_PARSE_ERROR"


class ConfigMissingField(ConfigError):
    default_code = "CONFIG_MISSING_FIELD"


class ConfigInvalid(ConfigError):
    default_code = "CONFIG_INVALID"


class InputError(FpsProbeError):
    default_code = "INPUT_ERROR"
    exit_code = 2


class ContainerError(FpsProbeError):
    default_code = "CONTAINER_ERROR"
    exit_code = 2


class TruncatedBoxError(ContainerError):
    default_code = "MP4_TRUNCATED_BOX"


class BoxFormatError(ContainerError):
    default_code = "MP4_BOX_FORMAT"


class ProbeError(ContainerError):
    default_code = "MP4_PROBE_ERROR"


class AdapterUnavailable(FpsProbeError):
    default_code = "ADAPTER_UNAVAILABLE"
    exit_code = 2


class LedgerError(FpsProbeError):
    default_code = "LEDGER_ERROR"
    exit_code = 3


class RunNotFound(LedgerError):
    default_code = "RUN_NOT_FOUND"
    exit_code = 2


class RunFinishedError(LedgerError):
    default_code = "RUN_FINISHED"
    exit_code = 3
