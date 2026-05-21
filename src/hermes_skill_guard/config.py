"""Configuration loading and safe defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir

_ALLOWED_ENFORCEMENT_MODES = {"audit", "candidate", "block"}


@dataclass(frozen=True, slots=True)
class EnforcementConfig:
    mode: str = "audit"
    timeout_ms: int = 150
    fail_open: bool = True


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    capture_raw_payloads: bool = False
    redaction_mode: str = "strict"
    max_field_length: int = 512
    hash_redacted_values: bool = True


@dataclass(frozen=True, slots=True)
class EventsConfig:
    ttl_days: int = 7
    max_rows: int = 10_000
    max_db_mb: int = 50
    rotate_every_n_writes: int = 100


@dataclass(frozen=True, slots=True)
class TraceCacheConfig:
    ttl_minutes: int = 10
    max_entries: int = 1000


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    """Optional metrics exporter configuration.

    ``enabled`` is the master switch; the OTel and Prometheus backends can
    independently be turned on. ``prometheus_port`` of ``0`` means "do not
    start an embedded HTTP server" — callers can still scrape the registry
    out-of-band if they wire it up themselves.
    """

    enabled: bool = False
    otel_enabled: bool = False
    prometheus_enabled: bool = False
    prometheus_port: int = 0


@dataclass(frozen=True, slots=True)
class AutoPromoteConfig:
    enabled: bool = False
    min_age_hours: int = 168  # 7 days
    require_no_conflicts: bool = True
    require_no_duplicates: bool = False
    dry_run: bool = True


@dataclass(frozen=True, slots=True)
class GuardConfig:
    dry_run: bool = True
    state_dir: Path = Path("~/.hermes/skill-guard")
    enabled_intents: set[str] = field(default_factory=set)
    enforcement: EnforcementConfig = EnforcementConfig()
    logging: LoggingConfig = LoggingConfig()
    events: EventsConfig = EventsConfig()
    trace_cache: TraceCacheConfig = TraceCacheConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    rules_path: Path | None = None
    auto_promote: AutoPromoteConfig = AutoPromoteConfig()

    @property
    def state_db(self) -> Path:
        return self.state_dir.expanduser() / "state.db"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _safe_int(value: Any, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return default
    return parsed


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _safe_mode(value: Any, default: str) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _ALLOWED_ENFORCEMENT_MODES:
            return normalized
    return default


def _parse_intent_set(value: str | list[str] | set[str] | None) -> set[str]:
    """Parse an intent set from env string or config list.

    Returns an empty set when *value* is ``None`` or empty, which means
    ALL intents are enabled (backward compatible default).
    """
    if value is None:
        return set()
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        return set(parts)
    if isinstance(value, set):
        return set(value)
    if isinstance(value, list):
        parts = [str(p).strip() for p in value if str(p).strip()]
        return set(parts)
    return set()


def _default_user_config_path() -> Path:
    """Return the default user configuration file path."""
    config_dir = Path(user_config_dir("hermes-skill-guard", appauthor=False))
    return config_dir / "config.yaml"


def _load_user_config_file(path: Path | None = None) -> dict[str, Any]:
    """Load user configuration from a YAML file.

    If *path* is not provided, checks ``SKILL_GUARD_CONFIG`` environment
    variable, then falls back to the platform default config directory.
    Returns an empty dict if the file does not exist or cannot be parsed.
    """
    if path is None:
        env_path = os.environ.get("SKILL_GUARD_CONFIG")
        path = Path(env_path) if env_path else _default_user_config_path()

    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
    except Exception:  # nosec B110 - intentional fallback to defaults on any parse/IO error
        pass
    return {}


def _merge_config(base: GuardConfig, user: dict[str, Any]) -> GuardConfig:
    """Merge user config dict into a base GuardConfig.

    Only explicit keys in the user config are applied; missing keys keep
    their base values.
    """
    enforcement = base.enforcement
    if "enforcement" in user and isinstance(user["enforcement"], dict):
        e = user["enforcement"]
        enforcement = EnforcementConfig(
            mode=_safe_mode(e.get("mode"), base.enforcement.mode),
            timeout_ms=_safe_int(
                e.get("timeout_ms"), base.enforcement.timeout_ms, minimum=1, maximum=10_000
            ),
            fail_open=_safe_bool(e.get("fail_open"), base.enforcement.fail_open),
        )

    logging_cfg = base.logging
    if "logging" in user and isinstance(user["logging"], dict):
        log_data = user["logging"]
        logging_cfg = LoggingConfig(
            capture_raw_payloads=log_data.get(
                "capture_raw_payloads", base.logging.capture_raw_payloads
            ),
            redaction_mode=log_data.get("redaction_mode", base.logging.redaction_mode),
            max_field_length=_safe_int(
                log_data.get("max_field_length"),
                base.logging.max_field_length,
                minimum=32,
                maximum=100_000,
            ),
            hash_redacted_values=log_data.get(
                "hash_redacted_values", base.logging.hash_redacted_values
            ),
        )

    events = base.events
    if "events" in user and isinstance(user["events"], dict):
        ev = user["events"]
        events = EventsConfig(
            ttl_days=_safe_int(ev.get("ttl_days"), base.events.ttl_days),
            max_rows=_safe_int(ev.get("max_rows"), base.events.max_rows),
            max_db_mb=_safe_int(ev.get("max_db_mb"), base.events.max_db_mb),
            rotate_every_n_writes=_safe_int(
                ev.get("rotate_every_n_writes"), base.events.rotate_every_n_writes
            ),
        )

    trace_cache = base.trace_cache
    if "trace_cache" in user and isinstance(user["trace_cache"], dict):
        tc = user["trace_cache"]
        trace_cache = TraceCacheConfig(
            ttl_minutes=_safe_int(tc.get("ttl_minutes"), base.trace_cache.ttl_minutes),
            max_entries=_safe_int(tc.get("max_entries"), base.trace_cache.max_entries),
        )

    observability = base.observability
    if "observability" in user and isinstance(user["observability"], dict):
        ob = user["observability"]
        observability = ObservabilityConfig(
            enabled=_safe_bool(ob.get("enabled"), base.observability.enabled),
            otel_enabled=_safe_bool(ob.get("otel_enabled"), base.observability.otel_enabled),
            prometheus_enabled=_safe_bool(
                ob.get("prometheus_enabled"), base.observability.prometheus_enabled
            ),
            prometheus_port=_safe_int(
                ob.get("prometheus_port"),
                base.observability.prometheus_port,
                minimum=0,
                maximum=65535,
            ),
        )

    state_dir = base.state_dir
    if "state_dir" in user and isinstance(user["state_dir"], str):
        state_dir = Path(user["state_dir"])

    dry_run = base.dry_run
    if "dry_run" in user and isinstance(user["dry_run"], bool):
        dry_run = user["dry_run"]

    enabled_intents = base.enabled_intents
    if "intents" in user and isinstance(user["intents"], dict):
        intents_data = user["intents"]
        enabled_intents = _parse_intent_set(intents_data.get("enabled"))

    rules_path = base.rules_path
    if "rules_path" in user and isinstance(user["rules_path"], str):
        rules_path = Path(user["rules_path"])

    auto_promote = base.auto_promote
    if "auto_promote" in user and isinstance(user["auto_promote"], dict):
        ap = user["auto_promote"]
        auto_promote = AutoPromoteConfig(
            enabled=_safe_bool(ap.get("enabled"), base.auto_promote.enabled),
            min_age_hours=_safe_int(
                ap.get("min_age_hours"), base.auto_promote.min_age_hours, minimum=0
            ),
            require_no_conflicts=_safe_bool(
                ap.get("require_no_conflicts"), base.auto_promote.require_no_conflicts
            ),
            require_no_duplicates=_safe_bool(
                ap.get("require_no_duplicates"), base.auto_promote.require_no_duplicates
            ),
            dry_run=_safe_bool(ap.get("dry_run"), base.auto_promote.dry_run),
        )

    return GuardConfig(
        dry_run=dry_run,
        state_dir=state_dir,
        enabled_intents=enabled_intents,
        enforcement=enforcement,
        logging=logging_cfg,
        events=events,
        trace_cache=trace_cache,
        observability=observability,
        rules_path=rules_path,
        auto_promote=auto_promote,
    )


def load_config(user_config_path: Path | None = None) -> GuardConfig:
    """Load config with fixed priority: environment > user config > defaults.

    Args:
        user_config_path: Optional explicit path to a user config YAML file.
            If omitted, checks ``SKILL_GUARD_CONFIG`` env var, then the
            platform default config directory.
    """
    # 1. Build defaults
    defaults = GuardConfig()

    # 2. Apply user config file (if present)
    user_data = _load_user_config_file(user_config_path)
    merged = _merge_config(defaults, user_data)

    # 3. Environment variables override everything
    state_dir = Path(os.environ.get("SKILL_GUARD_STATE_DIR", str(merged.state_dir)))
    dry_run = _env_bool("SKILL_GUARD_DRY_RUN", merged.dry_run)
    enabled_intents = _parse_intent_set(
        os.environ.get("SKILL_GUARD_ENABLED_INTENTS") or merged.enabled_intents
    )
    enforcement = EnforcementConfig(
        mode=_safe_mode(os.environ.get("SKILL_GUARD_ENFORCEMENT_MODE"), merged.enforcement.mode),
        timeout_ms=_safe_int(
            _env_int("SKILL_GUARD_PREFLIGHT_TIMEOUT_MS", merged.enforcement.timeout_ms),
            merged.enforcement.timeout_ms,
            minimum=1,
            maximum=10_000,
        ),
        fail_open=_env_bool("SKILL_GUARD_FAIL_OPEN", merged.enforcement.fail_open),
    )
    logging_config = LoggingConfig(
        capture_raw_payloads=_env_bool(
            "SKILL_GUARD_CAPTURE_RAW_PAYLOADS", merged.logging.capture_raw_payloads
        ),
        redaction_mode=os.environ.get("SKILL_GUARD_REDACTION_MODE", merged.logging.redaction_mode),
        max_field_length=_safe_int(
            _env_int("SKILL_GUARD_MAX_FIELD_LENGTH", merged.logging.max_field_length),
            merged.logging.max_field_length,
            minimum=32,
            maximum=100_000,
        ),
        hash_redacted_values=_env_bool(
            "SKILL_GUARD_HASH_REDACTED_VALUES", merged.logging.hash_redacted_values
        ),
    )
    trace_cache = TraceCacheConfig(
        ttl_minutes=_safe_int(
            _env_int("SKILL_GUARD_TRACE_CACHE_TTL_MINUTES", merged.trace_cache.ttl_minutes),
            merged.trace_cache.ttl_minutes,
        ),
        max_entries=_safe_int(
            _env_int("SKILL_GUARD_TRACE_CACHE_MAX_ENTRIES", merged.trace_cache.max_entries),
            merged.trace_cache.max_entries,
        ),
    )
    events = EventsConfig(
        ttl_days=_safe_int(
            _env_int("SKILL_GUARD_EVENTS_TTL_DAYS", merged.events.ttl_days),
            merged.events.ttl_days,
        ),
        max_rows=_safe_int(
            _env_int("SKILL_GUARD_EVENTS_MAX_ROWS", merged.events.max_rows),
            merged.events.max_rows,
        ),
        max_db_mb=_safe_int(
            _env_int("SKILL_GUARD_EVENTS_MAX_DB_MB", merged.events.max_db_mb),
            merged.events.max_db_mb,
        ),
        rotate_every_n_writes=_safe_int(
            _env_int("SKILL_GUARD_EVENTS_ROTATE_EVERY", merged.events.rotate_every_n_writes),
            merged.events.rotate_every_n_writes,
        ),
    )

    otel_enabled = _env_bool("HSG_OTEL_ENABLED", merged.observability.otel_enabled)
    prometheus_enabled = _env_bool(
        "HSG_PROMETHEUS_ENABLED", merged.observability.prometheus_enabled
    )
    prometheus_port = _safe_int(
        _env_int("HSG_PROMETHEUS_PORT", merged.observability.prometheus_port),
        merged.observability.prometheus_port,
        minimum=0,
        maximum=65535,
    )
    # If any backend is on, observability is implicitly enabled. Also respect
    # an explicit HSG_OBSERVABILITY_ENABLED override.
    observability_enabled = _env_bool(
        "HSG_OBSERVABILITY_ENABLED",
        merged.observability.enabled or otel_enabled or prometheus_enabled,
    )
    observability = ObservabilityConfig(
        enabled=observability_enabled,
        otel_enabled=otel_enabled,
        prometheus_enabled=prometheus_enabled,
        prometheus_port=prometheus_port,
    )

    rules_path_env = os.environ.get("HSG_RULES_PATH")
    if rules_path_env and rules_path_env.strip():
        rules_path: Path | None = Path(rules_path_env.strip())
    else:
        rules_path = merged.rules_path

    auto_promote = AutoPromoteConfig(
        enabled=_env_bool("SKILL_GUARD_AUTO_PROMOTE_ENABLED", merged.auto_promote.enabled),
        min_age_hours=_safe_int(
            _env_int("SKILL_GUARD_AUTO_PROMOTE_MIN_AGE_HOURS", merged.auto_promote.min_age_hours),
            merged.auto_promote.min_age_hours,
            minimum=0,
        ),
        require_no_conflicts=_env_bool(
            "SKILL_GUARD_AUTO_PROMOTE_NO_CONFLICTS",
            merged.auto_promote.require_no_conflicts,
        ),
        require_no_duplicates=_env_bool(
            "SKILL_GUARD_AUTO_PROMOTE_NO_DUPLICATES",
            merged.auto_promote.require_no_duplicates,
        ),
        dry_run=_env_bool("SKILL_GUARD_AUTO_PROMOTE_DRY_RUN", merged.auto_promote.dry_run),
    )

    return GuardConfig(
        dry_run=dry_run,
        state_dir=state_dir,
        enabled_intents=enabled_intents,
        enforcement=enforcement,
        logging=logging_config,
        events=events,
        trace_cache=trace_cache,
        observability=observability,
        rules_path=rules_path,
        auto_promote=auto_promote,
    )
