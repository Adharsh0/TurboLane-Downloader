# """
# turbolane/rl/storage.py

# Q-table persistence with atomic writes, versioned metadata, and backup rotation.

# Design principles:
# - Atomic saves: write to .tmp first, then rename (no corrupt Q-tables)
# - Automatic backup before every save
# - Human-readable JSON format for inspection and debugging
# - No application code. No sockets. Pure I/O.
# """

# import json
# import os
# import time
# import logging

# logger = logging.getLogger(__name__)

# SCHEMA_VERSION = "1.0"


# class QTableStorage:
#     """
#     Handles Q-table loading and saving with configurable path,
#     atomic writes, and automatic backup rotation.

#     Usage:
#         storage = QTableStorage(model_dir="models/edge")
#         Q, meta = storage.load()
#         storage.save(Q, stats)
#     """

#     def __init__(
#         self,
#         model_dir: str = "models/edge",
#         table_filename: str = "q_table.json",
#         backup_filename: str = "q_table.backup.json",
#     ):
#         self.model_dir = model_dir
#         self.table_path = os.path.join(model_dir, table_filename)
#         self.backup_path = os.path.join(model_dir, backup_filename)
#         self._tmp_path = self.table_path + ".tmp"

#         os.makedirs(model_dir, exist_ok=True)
#         logger.debug("QTableStorage ready: %s", self.table_path)

#     # -----------------------------------------------------------------------
#     # Save
#     # -----------------------------------------------------------------------

#     def save(self, Q: dict, stats: dict) -> bool:
#         """
#         Persist Q-table and agent stats to disk.

#         Returns:
#             True on success, False on failure (never raises).
#         """
#         try:
#             serialized_q = {
#                 str(state): {str(a): q for a, q in actions.items()}
#                 for state, actions in Q.items()
#             }

#             payload = {
#                 "schema_version": SCHEMA_VERSION,
#                 "saved_at": time.time(),
#                 "saved_at_human": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
#                 "q_table": serialized_q,
#                 "stats": stats,
#             }

#             with open(self._tmp_path, "w", encoding="utf-8") as f:
#                 json.dump(payload, f, indent=2)

#             if os.path.exists(self.table_path):
#                 os.replace(self.table_path, self.backup_path)

#             os.replace(self._tmp_path, self.table_path)

#             logger.info("Q-table saved: %d states → %s", len(Q), self.table_path)
#             return True

#         except Exception as e:
#             logger.error("Failed to save Q-table: %s", e)
#             if os.path.exists(self._tmp_path):
#                 try:
#                     os.remove(self._tmp_path)
#                 except Exception:
#                     pass
#             return False

#     # -----------------------------------------------------------------------
#     # Load
#     # -----------------------------------------------------------------------

#     def load(self) -> tuple[dict, dict]:
#         """
#         Load Q-table and metadata from disk.

#         Returns:
#             (Q, metadata) — returns ({}, {}) if no file exists or load fails.
#         """
#         for path, label in [(self.table_path, "primary"), (self.backup_path, "backup")]:
#             result = self._try_load(path, label)
#             if result is not None:
#                 return result

#         logger.info("No Q-table found at %s — starting fresh", self.model_dir)
#         return {}, {}

#     def _try_load(self, path: str, label: str) -> tuple[dict, dict] | None:
#         if not os.path.exists(path):
#             return None

#         try:
#             with open(path, "r", encoding="utf-8") as f:
#                 payload = json.load(f)

#             raw_q = payload.get("q_table", {})
#             metadata = payload.get("stats", {})

#             Q: dict[tuple, dict[int, float]] = {}
#             for state_str, actions in raw_q.items():
#                 try:
#                     state = tuple(int(x) for x in state_str.strip("()").split(",") if x.strip())
#                     Q[state] = {int(a): float(q) for a, q in actions.items()}
#                 except Exception as parse_err:
#                     logger.warning("Skipping malformed state '%s': %s", state_str, parse_err)
#                     continue

#             logger.info("Q-table loaded (%s): %d states from %s", label, len(Q), path)
#             return Q, metadata

#         except json.JSONDecodeError as e:
#             logger.warning("Corrupted Q-table at %s: %s", path, e)
#             return None
#         except Exception as e:
#             logger.warning("Could not load Q-table from %s: %s", path, e)
#             return None

#     # -----------------------------------------------------------------------
#     # Utility
#     # -----------------------------------------------------------------------

#     def exists(self) -> bool:
#         return os.path.exists(self.table_path)

#     def delete(self) -> None:
#         for path in (self.table_path, self.backup_path, self._tmp_path):
#             if os.path.exists(path):
#                 os.remove(path)
#         logger.info("Q-table files deleted from %s", self.model_dir)

#     def __repr__(self) -> str:
#         return f"QTableStorage(model_dir={self.model_dir!r}, exists={self.exists()})"
"""
turbolane/rl/storage.py

Q-table persistence with atomic writes, versioned metadata, and backup rotation.

Design principles:
- Atomic saves: write to .tmp first, then rename (no corrupt Q-tables)
- Automatic backup before every save
- Human-readable JSON format for inspection and debugging
- No application code. No sockets. Pure I/O.
"""

import json
import os
import time
import logging

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"


class QTableStorage:
    """
    Handles Q-table loading and saving with configurable path,
    atomic writes, and automatic backup rotation.

    Usage:
        storage = QTableStorage(model_dir="models/edge")
        Q, meta = storage.load()
        storage.save(Q, stats)
    """

    def __init__(
        self,
        model_dir: str = "models/edge",
        table_filename: str = "q_table.json",
        backup_filename: str = "q_table.backup.json",
    ):
        # --------------------------------------------------
        # Detect if running as EXE (PyInstaller)
        # --------------------------------------------------
        if getattr(__import__("sys"), "frozen", False):
            # Desktop installed version → use AppData
            appdata = os.getenv("APPDATA") or os.path.expanduser("~")
            model_dir = os.path.join(appdata, "TurboLane", "models", "edge")

        self.model_dir = model_dir
        self.table_path = os.path.join(model_dir, table_filename)
        self.backup_path = os.path.join(model_dir, backup_filename)
        self._tmp_path = self.table_path + ".tmp"

        os.makedirs(model_dir, exist_ok=True)
        logger.info("QTableStorage ready: %s", self.table_path)

    # -----------------------------------------------------------------------
    # Save
    # -----------------------------------------------------------------------

    def save(self, Q: dict, stats: dict) -> bool:
        """
        Persist Q-table and agent stats to disk.

        Returns:
            True on success, False on failure (never raises).
        """
        try:
            serialized_q = {
                str(state): {str(a): q for a, q in actions.items()}
                for state, actions in Q.items()
            }

            payload = {
                "schema_version": SCHEMA_VERSION,
                "saved_at": time.time(),
                "saved_at_human": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "q_table": serialized_q,
                "stats": stats,
            }

            with open(self._tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

            if os.path.exists(self.table_path):
                os.replace(self.table_path, self.backup_path)

            os.replace(self._tmp_path, self.table_path)

            logger.info("Q-table saved: %d states → %s", len(Q), self.table_path)
            return True

        except Exception as e:
            logger.error("Failed to save Q-table: %s", e)
            if os.path.exists(self._tmp_path):
                try:
                    os.remove(self._tmp_path)
                except Exception:
                    pass
            return False

    # -----------------------------------------------------------------------
    # Load
    # -----------------------------------------------------------------------

    def load(self) -> tuple[dict, dict]:
        """
        Load Q-table and metadata from disk.

        Returns:
            (Q, metadata) — returns ({}, {}) if no file exists or load fails.
        """
        for path, label in [(self.table_path, "primary"), (self.backup_path, "backup")]:
            result = self._try_load(path, label)
            if result is not None:
                return result

        logger.info("No Q-table found at %s — starting fresh", self.model_dir)
        return {}, {}

    def _try_load(self, path: str, label: str) -> tuple[dict, dict] | None:
        if not os.path.exists(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            raw_q = payload.get("q_table", {})
            metadata = payload.get("stats", {})

            Q: dict[tuple, dict[int, float]] = {}
            for state_str, actions in raw_q.items():
                try:
                    # Parse state like "(3,0,0)" into tuple (3,0,0)
                    # Remove parentheses and split
                    state = tuple(int(x) for x in state_str.strip("()").split(",") if x.strip())
                    Q[state] = {int(a): float(q) for a, q in actions.items()}
                except Exception as parse_err:
                    logger.warning("Skipping malformed state '%s': %s", state_str, parse_err)
                    continue

            logger.info("Q-table loaded (%s): %d states from %s", label, len(Q), path)
            return Q, metadata

        except json.JSONDecodeError as e:
            logger.warning("Corrupted Q-table at %s: %s", path, e)
            return None
        except Exception as e:
            logger.warning("Could not load Q-table from %s: %s", path, e)
            return None

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    def exists(self) -> bool:
        """Check if primary Q-table file exists."""
        return os.path.exists(self.table_path)

    def delete(self) -> None:
        """Delete all Q-table related files."""
        for path in (self.table_path, self.backup_path, self._tmp_path):
            if os.path.exists(path):
                os.remove(path)
        logger.info("Q-table files deleted from %s", self.model_dir)

    def __repr__(self) -> str:
        return f"QTableStorage(model_dir={self.model_dir!r}, exists={self.exists()})"