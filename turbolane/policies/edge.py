
"""
turbolane/policies/edge.py

EdgePolicy — the policy wrapper for edge / public internet environments.
"""

import logging
from turbolane.rl.agent import RLAgent
from turbolane.rl.storage import QTableStorage

logger = logging.getLogger(__name__)


class EdgePolicy:

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
        learning_rate: float = 0.2,
        discount_factor: float = 0.8,
        exploration_rate: float = 0.25,
        exploration_decay: float = 0.99,
        min_exploration: float = 0.08,
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

    def decide(self, throughput_mbps, rtt_ms, loss_pct):
        return self._agent.make_decision(throughput_mbps, rtt_ms, loss_pct)

    def learn(self, throughput_mbps, rtt_ms, loss_pct):
        self._agent.learn_from_feedback(throughput_mbps, rtt_ms, loss_pct)
        if (
            self._auto_save_every > 0
            and self._agent.total_updates > 0
            and self._agent.total_updates % self._auto_save_every == 0
        ):
            self.save()

    def save(self):
        return self._storage.save(self._agent.Q, self._agent.get_stats())

    def get_stats(self):
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

    def reset(self):
        self._agent.reset()
        logger.info("EdgePolicy: agent state reset")

    @property
    def current_connections(self):
        return self._agent.current_connections

    @property
    def agent(self):
        return self._agent

    def _discretize_state(self, throughput_mbps, rtt_ms, loss_pct):
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

        if rtt_ms < 30:
            r = 0
        elif rtt_ms < 80:
            r = 1
        elif rtt_ms < 150:
            r = 2
        else:
            r = 3

        if loss_pct < 0.2:
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

    def _compute_reward(self, prev_throughput, curr_throughput, curr_loss_pct, curr_rtt_ms, num_streams):
        # Throughput delta — small honest signal
        tput_delta = (curr_throughput - prev_throughput) * 0.05

        # Loss/RTT penalties
        loss_penalty = (curr_loss_pct ** 2) * 0.5
        rtt_penalty = max(0.0, (curr_rtt_ms - 50.0) * 0.01)

        # Stream range reward — the dominant signal
        if self.OPTIMAL_MIN <= num_streams <= self.OPTIMAL_MAX:
            stream_reward = 3.0                                        # strong: stay here
        elif num_streams < self.OPTIMAL_MIN:
            stream_reward = -2.0 * (self.OPTIMAL_MIN - num_streams)   # penalty below range
        elif num_streams <= self.EXTENDED_MAX:
            stream_reward = 1.0                                        # mild: extended range ok
        else:
            stream_reward = -1.0 * (num_streams - self.EXTENDED_MAX)  # too many streams

        reward = tput_delta + stream_reward - loss_penalty - rtt_penalty

        return max(-5.0, min(5.0, reward))

    def _apply_constraints(self, proposed_connections, current_connections, recent_metrics):
        result = max(self._min_connections, min(self._max_connections, proposed_connections))

        if not recent_metrics:
            return result

        avg_throughput = sum(m["throughput"] for m in recent_metrics) / len(recent_metrics)
        avg_loss = sum(m["loss"] for m in recent_metrics) / len(recent_metrics)
        avg_rtt = sum(m["rtt"] for m in recent_metrics) / len(recent_metrics)

        # Good conditions: hard floor at OPTIMAL_MIN, never go below it
        if avg_throughput > 10 and avg_loss < 1.0 and avg_rtt < 200:
            if result < self.OPTIMAL_MIN:
                logger.debug(
                    "EdgePolicy: good conditions (tput=%.1f, loss=%.2f%%, rtt=%.1f), "
                    "floor enforced at OPTIMAL_MIN=%d",
                    avg_throughput, avg_loss, avg_rtt, self.OPTIMAL_MIN,
                )
                return self.OPTIMAL_MIN

            # Cap increases at EXTENDED_MAX during good conditions
            if result > self.EXTENDED_MAX and proposed_connections > current_connections:
                logger.debug(
                    "EdgePolicy: good conditions, capping increase at EXTENDED_MAX=%d",
                    self.EXTENDED_MAX,
                )
                return min(self.EXTENDED_MAX, result)

        # Poor conditions: limit increases to +1
        elif avg_loss > 2.0 or avg_rtt > 200:
            if proposed_connections > current_connections:
                logger.debug(
                    "EdgePolicy: poor conditions (loss=%.2f%%, rtt=%.1f), "
                    "limiting increase to +1",
                    avg_loss, avg_rtt,
                )
                return min(current_connections + 1, result)

        return result

    def _load(self):
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

    def __repr__(self):
        return (
            f"EdgePolicy("
            f"connections={self.current_connections}, "
            f"q_states={len(self._agent.Q)}, "
            f"ε={self._agent.exploration_rate:.4f})"
        )