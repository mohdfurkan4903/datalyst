from datalyst.connectors.validation import SelectOnlyValidator

# Functions that block, take locks, read server files or run host commands.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "sleep",
        "benchmark",
        "load_file",
        "get_lock",
        "release_lock",
        "release_all_locks",
        "is_free_lock",
        "is_used_lock",
        "master_pos_wait",
        "source_pos_wait",
        "wait_for_executed_gtid_set",
        "wait_until_sql_thread_after_gtids",
        "sys_eval",
        "sys_exec",
        "set_config",
    }
)


class MySQLQueryValidator(SelectOnlyValidator):
    def __init__(self) -> None:
        super().__init__("mysql", FORBIDDEN_FUNCTIONS)
