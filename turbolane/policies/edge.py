"""
turbolane/policies/edge.py

EdgePolicy — the policy wrapper for edge / public internet environments.

Responsibilities:
- Own the RLAgent instance
- Own the QTableStorage instance
- Expose a clean, stable interface to the engine
- Handle persistence (auto-save, load on init)
- Apply edge-specific action constraints (optimal stream range for HTTP downloads)

Design principles:
- No networking code
- No application logic
- Agent-type-agnostic interface (same methods regardless of Q-learning or PPO)

Edge vs DCI differences:
- Finer throughput discretization (6 bins vs 5) for typical HTTP download speeds
- Optimal stream range awareness (6–10 streams for public CDN downloads)
- Action constraints tuned for high-RTT, variable-loss public internet conditions
- Progressive stream cost in reward to discourage unnecessary connections
"""

import logging
from pathlib import Path

from turbolane.rl.agent import RLAgent
from turbolane.rl.storage import QTableStorage

logger = logging.getLogger(__name__)


class EdgePolicy:
    """
    Policy for edge / public internet download environments.

    Public interface:
        decide(throughput, rtt, loss_pct)        → int  (stream count)
        learn(throughput, rtt, loss_pct)                (Q-update)
        save()                                          (persist to disk)
        get_stats()                              → dict
        reset()                                         (clear learned state)

    All methods have identical signatures regardless of whether
    the backend is Q-learning or PPO — future migration is transparent.
    """

    # Optimal stream range for public internet HTTP downloads
    OPTIMAL_MIN = 6
    OPTIMAL_MAX = 10
    OPTIMAL_BONUS = 12.0
    EXTENDED_MAX = 12
    EXTENDED_BONUS = 5.0

    def __init__(
        self,
        model_dir: str = "models/edge",
        min_connections: int = 1,
        max_connections: int = 16,
        default_connections: int = 8,
        learning_rate: float = 0.1,
        discount_factor: float = 0.8,
        exploration_rate: float = 0.3,
        exploration_decay: float = 0.995,
        min_exploration: float = 0.05,
        monitoring_interval: float = 5.0,
        auto_save_every: int = 50,
    ):
        self._auto_save_every = auto_save_every
        self._min_connections = min_connections
        self._max_connections = max_connections

        self._storage = QTableStorage(model_dir=model_dir)

        self._agent = RLAgent(
            min_connections=min_connections,
            max_connections=max_connections,
            default_connections=default_connections,
            learning_rate=learning_rate,
            discount_factor=discount_factor,
            exploration_rate=exploration_rate,
            exploration_decay=exploration_decay,
            min_exploration=min_exploration,
            monitoring_interval=monitoring_interval,
            discretize_fn=self._discretize_state,
            reward_fn=self._compute_reward,
            constraint_fn=self._apply_constraints,
        )

        self._load()

        logger.info(
            "EdgePolicy ready: model_dir=%s connections=[%d..%d] optimal=[%d..%d]",
            model_dir, min_connections, max_connections,
            self.OPTIMAL_MIN, self.OPTIMAL_MAX,
        )

    # -----------------------------------------------------------------------
    # Core interface
    # -----------------------------------------------------------------------

    def decide(self, throughput_mbps: float, rtt_ms: float, loss_pct: float) -> int:
        return self._agent.make_decision(throughput_mbps, rtt_ms, loss_pct)

    def learn(self, throughput_mbps: float, rtt_ms: float, loss_pct: float) -> None:
        self._agent.learn_from_feedback(throughput_mbps, rtt_ms, loss_pct)

        if (
            self._auto_save_every > 0
            and self._agent.total_updates > 0
            and self._agent.total_updates % self._auto_save_every == 0
        ):
            logger.debug("Auto-save triggered at update #%d", self._agent.total_updates)
            self.save()

    def save(self) -> bool:
        return self._storage.save(self._agent.Q, self._agent.get_stats())

    def get_stats(self) -> dict:
        stats = self._agent.get_stats()
        stats["model_dir"] = self._storage.model_dir
        stats["model_exists_on_disk"] = self._storage.exists()
        connections = self._agent.current_connections
        if self.OPTIMAL_MIN <= connections <= self.OPTIMAL_MAX:
            stats["stream_range_status"] = "optimal"
        elif connections <= self.EXTENDED_MAX:
            stats["stream_range_status"] = "extended"
        else:
            stats["stream_range_status"] = "above_optimal"
        return stats

    def reset(self) -> None:
        self._agent.reset()
        logger.info("EdgePolicy: agent state reset")

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def current_connections(self) -> int:
        return self._agent.current_connections

    @property
    def agent(self) -> RLAgent:
        return self._agent

    # -----------------------------------------------------------------------
    # Edge-specific policy functions (injected into RLAgent)
    # -----------------------------------------------------------------------

    def _discretize_state(
        self,
        throughput_mbps: float,
        rtt_ms: float,
        loss_pct: float,
    ) -> tuple:
        """
        Edge-tuned state discretization.

        Throughput bins (Mbps): 0-10, 10-20, 20-30, 30-40, 40-50, 50+
            6 bins — public CDN speeds cluster in the 10-50 Mbps range.
        RTT bins (ms): 0-50, 50-150, 150-300, 300-600, 600-1000, 1000+
            6 bins — extended range covers real-world high-latency connections.
            Previous 4-bin version capped at 150ms, causing all high-RTT
            connections to collapse into the same state and preventing
            Q-table growth.
        Loss bins (%): 0-0.1, 0.1-0.5, 0.5-1.0, 1.0-2.0, 2.0+
            5 bins — unchanged, loss thresholds are protocol-driven.

        Total states: 6 × 6 × 5 = 180
        """
        # Throughput level (0-5)
        if throughput_mbps < 10:
            t = 0
        elif throughput_mbps < 20:
            t = 1
        elif throughput_mbps < 30:
            t = 2
        elif throughput_mbps < 40:
            t = 3
        elif throughput_mbps < 50:
            t = 4
        else:
            t = 5

        # RTT level (0-5) — extended to cover real-world high-latency connections
        if rtt_ms < 50:
            r = 0
        elif rtt_ms < 150:
            r = 1
        elif rtt_ms < 300:
            r = 2
        elif rtt_ms < 600:
            r = 3
        elif rtt_ms < 1000:
            r = 4
        else:
            r = 5

        # Loss level (0-4)
        if loss_pct < 0.1:
            l = 0
        elif loss_pct < 0.5:
            l = 1
        elif loss_pct < 1.0:
            l = 2
        elif loss_pct < 2.0:
            l = 3
        else:
            l = 4

        return (t, r, l)

    def _compute_reward(
        self,
        prev_throughput: float,
        curr_throughput: float,
        curr_loss_pct: float,
        curr_rtt_ms: float,
        num_streams: int,
    ) -> float:
        """
        Edge reward function.

        Components:
          + throughput improvement (primary signal)
          − quadratic loss penalty
          − RTT penalty (congestion signal)
          − progressive stream cost (discourages unnecessary connections)
          + optimal range bonus (6-10 streams → strongest incentive)
          + extended range bonus (10-12 streams → moderate incentive)
        """
        # Throughput improvement
        tput_delta = curr_throughput - prev_throughput

        # Quadratic loss penalty
        loss_penalty = (curr_loss_pct ** 2) * 0.5

        # RTT congestion penalty — scaled for extended RTT range
        rtt_penalty = max(0.0, (curr_rtt_ms - 50.0) * 0.005)

        # Progressive stream overhead
        if num_streams <= self.OPTIMAL_MIN:
            stream_penalty = 0.0
        elif num_streams <= self.OPTIMAL_MAX:
            stream_penalty = (num_streams - self.OPTIMAL_MIN) * 0.1
        elif num_streams <= self.EXTENDED_MAX:
            stream_penalty = (num_streams - self.OPTIMAL_MAX) * 0.5 + 0.4
        else:
            stream_penalty = (num_streams - self.EXTENDED_MAX) * 1.5 + 1.4

        # Efficiency bonus: throughput per stream
        efficiency = curr_throughput / max(1, num_streams)
        efficiency_bonus = min(2.0, efficiency * 0.1) if efficiency > 4.0 else 0.0

        reward = (
            tput_delta * 0.1
            + efficiency_bonus
            - loss_penalty
            - rtt_penalty
            - stream_penalty
        )

        # Optimal range bonus
        if self.OPTIMAL_MIN <= num_streams <= self.OPTIMAL_MAX:
            reward += self.OPTIMAL_BONUS * 0.1
        elif num_streams <= self.EXTENDED_MAX:
            reward += self.EXTENDED_BONUS * 0.1

        return max(-5.0, min(5.0, reward))

    def _apply_constraints(
        self,
        proposed_connections: int,
        current_connections: int,
        recent_metrics: list,
    ) -> int:
        """
        Edge-specific action constraints.

        Guards:
        - Good conditions  → don't drop below OPTIMAL_MIN
        - Good conditions  → cap at EXTENDED_MAX
        - Poor conditions  → limit increases to +1
        """
        result = max(self._min_connections, min(self._max_connections, proposed_connections))

        if not recent_metrics:
            return result

        avg_throughput = sum(m["throughput"] for m in recent_metrics) / len(recent_metrics)
        avg_loss = sum(m["loss"] for m in recent_metrics) / len(recent_metrics)
        avg_rtt = sum(m["rtt"] for m in recent_metrics) / len(recent_metrics)

        # Good conditions: keep in optimal/extended range
        # RTT threshold raised from 150 → 600 to match new RTT bins
        if avg_throughput > 20 and avg_loss < 0.5 and avg_rtt < 600:
            if result < self.OPTIMAL_MIN and proposed_connections < current_connections:
                logger.debug("EdgePolicy: good conditions, floor at OPTIMAL_MIN=%d", self.OPTIMAL_MIN)
                return max(self.OPTIMAL_MIN, current_connections)
            if result > self.EXTENDED_MAX and proposed_connections > current_connections:
                logger.debug("EdgePolicy: good conditions, cap at EXTENDED_MAX=%d", self.EXTENDED_MAX)
                return min(self.EXTENDED_MAX, result)

        # Poor conditions: limit increases
        # RTT threshold raised from 200 → 1000 to match new RTT bins
        if avg_loss > 2.0 or avg_rtt > 1000:
            if proposed_connections > current_connections:
                logger.debug("EdgePolicy: poor conditions, limiting increase to +1")
                return min(current_connections + 1, result)

        return result

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _load(self) -> None:
        Q, metadata = self._storage.load()
        if Q:
            self._agent.Q = Q
            saved_epsilon = metadata.get("exploration_rate")
            if saved_epsilon is not None:
                self._agent.exploration_rate = max(
                    self._agent.min_exploration,
                    float(saved_epsilon),
                )
            self._agent.total_decisions = int(metadata.get("total_decisions", 0))
            self._agent.total_updates = int(metadata.get("total_updates", 0))
            logger.info(
                "EdgePolicy restored: %d Q-states, %d decisions, ε=%.4f",
                len(Q),
                self._agent.total_decisions,
                self._agent.exploration_rate,
            )

    def __repr__(self) -> str:
        return (
            f"EdgePolicy("
            f"connections={self.current_connections}, "
            f"q_states={len(self._agent.Q)}, "
            f"ε={self._agent.exploration_rate:.4f})"
        )