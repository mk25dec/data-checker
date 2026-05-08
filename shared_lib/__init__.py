"""Shared tools for data processor modules."""

# NOTE: Main import list for shared tools.

from .cli_common import CommonIoCliOptions, effective_quiet, validate_output_targets_exclusive
from .cli_token_lists import (
    parse_comma_separated_cli_tokens,
    parse_json_array_of_strings,
    resolve_comma_or_json_cli_list,
)
from .atomic_write import (
    write_bytes_atomic,
    write_json_atomic,
    write_text_atomic,
)
from .config_utils import (
    expand_env_vars,
    load_toml_config,
    load_yaml_config,
    merge_settings,
    pick_setting,
    validate_allowed_settings,
    validate_required_settings,
)
from .dataframe_io import write_dataframe_atomic
from .duckdb_create_load import DUCKDB_LOAD_SUPPORTED_FORMATS, DuckdbResourceLimits, load_file_into_database
from .duckdb_sql import sql_double_quoted_ident, sql_string_literal
from .parquet_input_paths import (
    PARQUET_SUFFIXES,
    load_manifest_parquet_paths,
    resolve_parquet_file_inputs,
)
from .find_latest_file import SortBy, find_latest_file, find_latest_files
from .failfast import SharedResultError, exit_code, first_failed, raise_if_failed, stop_on_first_failure
from .file_size import get_directory_size, get_file_size, get_path_size, human_readable_size
from .format_registry import detect_format_from_path, normalize_format_token, supported_formats_hint, validate_format
from .logging_utils import get_logger, log_level, log_result
from .naming import add_timestamp_to_path, apply_output_format_to_path, normalize_file_stem, timestamp_compact
from .output_paths import default_output_beside_input, make_output_file_path, make_output_folder, make_run_id
from .parquet_row_audit import (
    blankish_cell_sql,
    blankish_cell_sql_expr,
    blank_rows_union_sql,
    blank_rows_union_sql_exprs,
    dedupe_partition_expressions,
    export_tagged_data_report_csv,
    keep_row_if_not_blank_sql_exprs,
    normalized_merge_key_sql,
    run_parquet_row_audit,
    write_parquet_row_audit_report,
)
from .path_utils import (
    PathCheck,
    check_path,
    ensure_directory,
    ensure_parent_directory,
    require_directory,
    require_file,
    resolve_path,
)
from .results import (
    EXIT_ERROR,
    EXIT_IO_ERROR,
    EXIT_NOT_FOUND,
    EXIT_SUCCESS,
    EXIT_TYPE_MISMATCH,
    EXIT_VALIDATION_ERROR,
    SharedResult,
    failure,
    success,
)
from .time_utils import (
    format_duration,
    format_time_short,
    now_local,
    now_utc,
    parse_iso_timestamp,
    run_stats,
    timestamp_compact_microseconds,
    timestamp_iso,
)
from .generate_uid import generate_nanoid, generate_uid, generate_uuid
from .validate_input import validate_input_file
from .validate_output import validate_output_file

__all__ = [
    "EXIT_ERROR",
    "EXIT_IO_ERROR",
    "EXIT_NOT_FOUND",
    "EXIT_SUCCESS",
    "EXIT_TYPE_MISMATCH",
    "EXIT_VALIDATION_ERROR",
    "PathCheck",
    "SharedResult",
    "SharedResultError",
    "SortBy",
    "add_timestamp_to_path",
    "apply_output_format_to_path",
    "check_path",
    "CommonIoCliOptions",
    "default_output_beside_input",
    "detect_format_from_path",
    "DUCKDB_LOAD_SUPPORTED_FORMATS",
    "DuckdbResourceLimits",
    "ensure_directory",
    "effective_quiet",
    "ensure_parent_directory",
    "exit_code",
    "expand_env_vars",
    "failure",
    "find_latest_file",
    "find_latest_files",
    "first_failed",
    "format_duration",
    "format_time_short",
    "get_directory_size",
    "get_file_size",
    "get_logger",
    "get_path_size",
    "generate_nanoid",
    "generate_uid",
    "generate_uuid",
    "human_readable_size",
    "load_file_into_database",
    "load_manifest_parquet_paths",
    "load_toml_config",
    "load_yaml_config",
    "log_level",
    "log_result",
    "make_output_file_path",
    "make_output_folder",
    "make_run_id",
    "merge_settings",
    "now_local",
    "now_utc",
    "normalize_file_stem",
    "normalize_format_token",
    "parse_iso_timestamp",
    "PARQUET_SUFFIXES",
    "blankish_cell_sql",
    "blankish_cell_sql_expr",
    "blank_rows_union_sql",
    "blank_rows_union_sql_exprs",
    "dedupe_partition_expressions",
    "export_tagged_data_report_csv",
    "keep_row_if_not_blank_sql_exprs",
    "normalized_merge_key_sql",
    "pick_setting",
    "raise_if_failed",
    "require_directory",
    "require_file",
    "resolve_path",
    "resolve_parquet_file_inputs",
    "run_parquet_row_audit",
    "run_stats",
    "sql_double_quoted_ident",
    "sql_string_literal",
    "stop_on_first_failure",
    "success",
    "supported_formats_hint",
    "timestamp_compact",
    "timestamp_compact_microseconds",
    "timestamp_iso",
    "validate_format",
    "validate_output_targets_exclusive",
    "parse_comma_separated_cli_tokens",
    "parse_json_array_of_strings",
    "resolve_comma_or_json_cli_list",
    "validate_input_file",
    "validate_allowed_settings",
    "validate_output_file",
    "validate_required_settings",
    "write_bytes_atomic",
    "write_dataframe_atomic",
    "write_parquet_row_audit_report",
    "write_json_atomic",
    "write_text_atomic",
]
