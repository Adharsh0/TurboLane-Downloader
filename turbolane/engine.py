"""
turbolane/engine.py

TurboLaneEngine — the single public entry point for the TurboLane SDK.

This is the ONLY class that application code (or the adapter) should import.
All policy selection, agent wiring, and mode routing happens here.

Supported modes:
    'client' → EdgePolicy (public internet / edge downloads)

Supported algorithms:
    'qlearning' → RLAgent

Usage (client + Q-learning):
    from turbolane.engine import TurboLaneEngine

    engine = TurboLaneEngine(mode='client', algorithm='qlearning')
    streams = engine.decide(throughput_mbps, rtt_ms, loss_pct)
    engine.learn(throughput_mbps, rtt_ms, loss_pct)
    engine.save()
"""

import logging

logger = logging.getLogger(__name__)

_VALID_ALGORITHMS = {"qlearning", "ppo"}


class TurboLaneEngine:
    """
    Unified TurboLane control-plane engine for download manager.

    Public interface (identical regardless of algorithm):
        decide(throughput_mbps, rtt_ms, loss_pct)  → int
        learn(throughput_mbps, rtt_ms, loss_pct)
        save()                                      → bool
        get_stats()                                 → dict
        reset()

    Convenience properties:
        .current_connections                        → int
        .mode                                       → str
        .algorithm                                  → str
    """

    def __init__(
        self,
        mode: str = "client",
        algorithm: str = "qlearning",
        **policy_kwargs,
    ):
        """
        Initialize TurboLane engine.

        Args:
            mode:          'client' (edge/public internet)
            algorithm:     'qlearning' or 'ppo'
            **policy_kwargs: Passed directly to the policy constructor.
                           See EdgePolicy.__init__ for valid keys.

        Example:
            TurboLaneEngine(
                mode='client',
                algorithm='qlearning',
                model_dir='models/edge',
                min_connections=1,
                max_connections=16,
                default_connections=8,
                monitoring_interval=5.0,
            )
        """
        mode = mode.lower()
        algorithm = algorithm.lower().replace("-", "").replace("_", "")

        if mode != "client":
            raise ValueError(
                f"Unknown mode '{mode}'. This engine supports mode='client' only."
            )
        if algorithm not in _VALID_ALGORITHMS:
            raise ValueError(
                f"Unknown algorithm '{algorithm}'. Valid: {list(_VALID_ALGORITHMS)}"
            )

        self.mode = mode
        self.algorithm = algorithm

        self._policy = self._build_policy(mode, algorithm, policy_kwargs)

        logger.info(
            "TurboLaneEngine ready: mode=%s algorithm=%s",
            self.mode, self.algorithm,
        )

    # -----------------------------------------------------------------------
    # Core interface — these are the ONLY methods the adapter calls
    # -----------------------------------------------------------------------

    def decide(
        self,
        throughput_mbps: float,
        rtt_ms: float,
        loss_pct: float,
    ) -> int:
        """
        Make a stream count recommendation based on current network metrics.

        Args:
            throughput_mbps: Observed throughput in Mbps
            rtt_ms:          Observed round-trip time in milliseconds
            loss_pct:        Observed packet loss in percent (0–100)

        Returns:
            Recommended number of parallel TCP streams (int)
        """
        return self._policy.decide(throughput_mbps, rtt_ms, loss_pct)

    def learn(
        self,
        throughput_mbps: float,
        rtt_ms: float,
        loss_pct: float,
    ) -> None:
        """
        Update the policy from the outcome of the previous decision.

        Call this once per monitoring cycle, AFTER decide(), with
        the metrics observed after the previous action took effect.

        Args:
            throughput_mbps: Current throughput in Mbps
            rtt_ms:          Current RTT in milliseconds
            loss_pct:        Current packet loss in percent (0–100)
        """
        self._policy.learn(throughput_mbps, rtt_ms, loss_pct)

    def save(self) -> bool:
        """
        Persist the policy to disk.

        Returns:
            True on success, False on failure.
        """
        return self._policy.save()

    def get_stats(self) -> dict:
        """Return a stats dict for logging, monitoring, and API display."""
        stats = self._policy.get_stats()
        stats["engine_mode"] = self.mode
        stats["engine_algorithm"] = self.algorithm
        return stats

    def reset(self) -> None:
        """Clear the policy's learned state. Does not delete files on disk."""
        self._policy.reset()

    # -----------------------------------------------------------------------
    # Convenience properties
    # -----------------------------------------------------------------------

    @property
    def current_connections(self) -> int:
        """Current recommended stream count."""
        return self._policy.current_connections

    # -----------------------------------------------------------------------
    # Internal factory
    # -----------------------------------------------------------------------

    def _build_policy(self, mode: str, algorithm: str, kwargs: dict):
        if mode == "client":
            from turbolane.policies.edge import EdgePolicy
            return EdgePolicy(**kwargs)
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def __repr__(self) -> str:
        return (
            f"TurboLaneEngine("
            f"mode={self.mode!r}, "
            f"algorithm={self.algorithm!r}, "
            f"connections={self.current_connections})"
        )
