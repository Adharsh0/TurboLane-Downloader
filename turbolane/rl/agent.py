"""
turbolane/rl/agent.py

Q-Learning agent for optimizing parallel TCP stream count.

Design principles:
- State: discretized tuple — shape defined by the policy via discretize_fn
- Actions: 5 discrete actions mapping to stream count deltas
- Reward: defined by the policy via reward_fn
- Constraints: defined by the policy via constraint_fn
- No application code. No sockets. No file I/O. Pure RL logic.

The policy injects three callables at init time:
    discretize_fn(throughput, rtt, loss) → state tuple
    reward_fn(prev_t, curr_t, loss, rtt, streams) → float
    constraint_fn(proposed, current, recent_metrics) → int

This makes RLAgent policy-agnostic — the same agent works for both
FederatedPolicy (DCI) and EdgePolicy (edge/internet) without modification.
"""

import random
import time
import logging
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action space: index → stream count delta
# ---------------------------------------------------------------------------
ACTIONS = {
    0: +2,   # aggressive increase
    1: +1,   # conservative increase
    2:  0,   # hold
    3: -1,   # conservative decrease
    4: -2,   # aggressive decrease
}
NUM_ACTIONS = len(ACTIONS)


class RLAgent:
    """
    Q-Learning agent for TCP stream count optimization.

    Public interface:
        make_decision(throughput, rtt, loss_pct)  → int
        learn_from_feedback(throughput, rtt, loss_pct)
        get_stats()                               → dict
        reset()
    """

    def __init__(
        self,
        min_connections: int = 1,
        max_connections: int = 16,
        default_connections: int = 8,
        learning_rate: float = 0.1,
        discount_factor: float = 0.8,
        exploration_rate: float = 0.3,
        exploration_decay: float = 0.995,
        min_exploration: float = 0.05,
        monitoring_interval: float = 5.0,
        discretize_fn: Optional[Callable] = None,
        reward_fn: Optional[Callable] = None,
        constraint_fn: Optional[Callable] = None,
    ):
        # Connection bounds
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.current_connections = default_connections

        # RL hyperparameters
        self.learning_rate = learning_rate
        self.discount_factor = discount_factor
        self.exploration_rate = exploration_rate
        self.exploration_decay = exploration_decay
        self.min_exploration = min_exploration

        # Monitoring interval
        self.monitoring_interval = monitoring_interval
        self._last_decision_time: float = 0.0
        self._last_learn_time: float = 0.0

        # Injected policy functions (with sensible defaults)
        self._discretize = discretize_fn or self._default_discretize
        self._reward = reward_fn or self._default_reward
        self._constrain = constraint_fn or self._default_constrain

        # Q-table
        self.Q: dict[tuple, dict[int, float]] = {}

        # Transition memory
        self._last_state: tuple | None = None
        self._last_action: int | None = None
        self._last_metrics: dict | None = None
        self._learn_pending: bool = False

        # Rolling history
        self._action_history: deque = deque(maxlen=10)
        self._metrics_history: deque = deque(maxlen=50)

        # Counters
        self.total_decisions: int = 0
        self.total_updates: int = 0
        self._total_reward: float = 0.0
        self._positive_rewards: int = 0
        self._negative_rewards: int = 0
        self._throughput_improvements: int = 0

        logger.info(
            "RLAgent init: connections=[%d..%d] default=%d "
            "lr=%.3f γ=%.2f ε=%.2f interval=%.1fs",
            min_connections, max_connections, default_connections,
            learning_rate, discount_factor, exploration_rate, monitoring_interval,
        )

    # -----------------------------------------------------------------------
    # Default policy functions (used if policy doesn't inject its own)
    # -----------------------------------------------------------------------

    def _default_discretize(self, throughput: float, rtt: float, loss: float) -> tuple:
        t = 0 if throughput < 10 else 1 if throughput < 50 else 2 if throughput < 100 else 3 if throughput < 500 else 4
        r = 0 if rtt < 30 else 1 if rtt < 80 else 2 if rtt < 150 else 3
        l = 0 if loss < 0.1 else 1 if loss < 0.5 else 2 if loss < 1.0 else 3 if loss < 2.0 else 4
        return (t, r, l)

    def _default_reward(self, prev_t, curr_t, loss, rtt, streams) -> float:
        tput_delta = curr_t - prev_t
        loss_penalty = (loss ** 2) * 0.1
        rtt_penalty = max(0.0, (rtt - 100.0) * 0.002)
        stream_penalty = 0.0 if streams <= 8 else (streams - 8) * 0.1
        return max(-5.0, min(5.0, tput_delta * 0.2 - loss_penalty - rtt_penalty - stream_penalty))

    def _default_constrain(self, proposed, current, recent_metrics) -> int:
        return max(self.min_connections, min(self.max_connections, proposed))

    # -----------------------------------------------------------------------
    # Q-table access
    # -----------------------------------------------------------------------

    def _init_state(self, state: tuple) -> None:
        if state not in self.Q:
            self.Q[state] = {a: 0.0 for a in range(NUM_ACTIONS)}

    def _get_q(self, state: tuple, action: int) -> float:
        self._init_state(state)
        return self.Q[state][action]

    def _set_q(self, state: tuple, action: int, value: float) -> None:
        self._init_state(state)
        self.Q[state][action] = max(-10.0, min(10.0, value))

    def _best_action(self, state: tuple) -> int:
        self._init_state(state)
        return max(self.Q[state], key=self.Q[state].__getitem__)

    def _max_q(self, state: tuple) -> float:
        # Don't call _init_state here — only peek if state already exists
        if state not in self.Q:
            return 0.0
        return max(self.Q[state].values())

    # -----------------------------------------------------------------------
    # Action selection
    # -----------------------------------------------------------------------

    def choose_action(self, state: tuple) -> int:
        """Epsilon-greedy with boosted exploration and oscillation damping."""
        self.exploration_rate = max(
            self.min_exploration,
            self.exploration_rate * self.exploration_decay,
        )

        visit_count = sum(1 for m in self._metrics_history if m.get("state") == state)
        effective_epsilon = (
            min(0.8, self.exploration_rate * 2.0)   # boosted from 0.6 → 0.8
            if visit_count < 5                       # extended from 3 → 5 visits
            else self.exploration_rate
        )

        if random.random() < effective_epsilon:
            # Bias random exploration toward increase actions (0, 1) on good connections
            return random.choices(
                population=[0, 1, 2, 3, 4],
                weights=[3, 3, 2, 1, 1],   # 60% chance of increase/hold, 40% decrease
                k=1
            )[0]

        best = self._best_action(state)

        if len(self._action_history) >= 4:
            recent = list(self._action_history)[-4:]
            increasing = {0, 1}
            decreasing = {3, 4}
            oscillating = (
                recent[0] in increasing and recent[1] in decreasing and
                recent[2] in increasing and recent[3] in decreasing
            )
            if oscillating:
                logger.debug("Oscillation detected — forcing hold action")
                return 2

        return best

    def _apply_action(self, action: int, current: int) -> int:
        delta = ACTIONS[action]
        proposed = current + delta
        recent = list(self._metrics_history)[-3:] if len(self._metrics_history) >= 3 else []
        return self._constrain(proposed, current, recent)

    # -----------------------------------------------------------------------
    # Q-table update (Bellman equation)
    # -----------------------------------------------------------------------

    def _update_q(self, state, action, reward, next_state) -> None:
        current_q = self._get_q(state, action)
        max_next_q = self._max_q(next_state)
        td_target = reward + self.discount_factor * max_next_q
        td_error = td_target - current_q

        effective_lr = self.learning_rate * 1.5 if abs(reward) > 1.0 else self.learning_rate
        new_q = current_q + effective_lr * td_error
        self._set_q(state, action, new_q)
        self.total_updates += 1

        logger.debug(
            "Q-update s=%s a=%d r=%.3f td_err=%.3f new_q=%.3f",
            state, action, reward, td_error, new_q,
        )

    # -----------------------------------------------------------------------
    # Public decision interface
    # -----------------------------------------------------------------------

    def should_decide(self) -> bool:
        return (time.monotonic() - self._last_decision_time) >= self.monitoring_interval

    def make_decision(
        self,
        throughput_mbps: float,
        rtt_ms: float,
        loss_pct: float,
    ) -> int:
        """Make a stream count decision. Self-gates on monitoring interval."""
        if not self.should_decide():
            return self.current_connections

        state = self._discretize(throughput_mbps, rtt_ms, loss_pct)
        action = self.choose_action(state)
        new_connections = self._apply_action(action, self.current_connections)

        self._last_state = state
        self._last_action = action
        self._last_metrics = {
            "throughput": throughput_mbps,
            "rtt": rtt_ms,
            "loss": loss_pct,
            "connections": self.current_connections,
            "state": state,
        }

        self.current_connections = new_connections
        self._last_decision_time = time.monotonic()
        self._learn_pending = True
        self.total_decisions += 1
        self._action_history.append(action)

        logger.info(
            "Decision #%d: streams=%d action=%d(%+d) ε=%.3f state=%s",
            self.total_decisions,
            self.current_connections,
            action,
            ACTIONS[action],
            self.exploration_rate,
            state,
        )

        return self.current_connections

    def learn_from_feedback(
        self,
        throughput_mbps: float,
        rtt_ms: float,
        loss_pct: float,
    ) -> None:
        """Update Q-table using outcome of the previous decision.

        Gated so it only fires once per decision cycle — rapid calls
        between decisions are ignored to prevent multiple Q-updates
        for a single action.
        """
        now = time.monotonic()

        # Gate: only allow one learn per monitoring interval
        if (now - self._last_learn_time) < self.monitoring_interval:
            return

        # Gate: only learn if a decision was actually made since last learn
        if not self._learn_pending:
            return

        if self._last_state is None or self._last_action is None or self._last_metrics is None:
            self._last_metrics = {
                "throughput": throughput_mbps,
                "rtt": rtt_ms,
                "loss": loss_pct,
                "connections": self.current_connections,
                "state": self._discretize(throughput_mbps, rtt_ms, loss_pct),
            }
            return

        prev = self._last_metrics
        reward = self._reward(
            prev["throughput"],
            throughput_mbps,
            loss_pct,
            rtt_ms,
            self.current_connections,
        )

        next_state = self._discretize(throughput_mbps, rtt_ms, loss_pct)
        self._update_q(self._last_state, self._last_action, reward, next_state)

        self._total_reward += reward
        if reward > 0:
            self._positive_rewards += 1
        else:
            self._negative_rewards += 1
        if throughput_mbps > prev["throughput"]:
            self._throughput_improvements += 1

        self._metrics_history.append({
            "state": self._last_state,
            "action": self._last_action,
            "reward": reward,
            "throughput": throughput_mbps,
            "rtt": rtt_ms,
            "loss": loss_pct,
            "connections": self.current_connections,
        })

        # Reset transition memory after learn — prevents cross-download contamination
        self._last_state = None
        self._last_action = None
        self._last_metrics = None
        self._learn_pending = False
        self._last_learn_time = now

    # -----------------------------------------------------------------------
    # Stats and reset
    # -----------------------------------------------------------------------

    def get_stats(self) -> dict:
        avg_reward = self._total_reward / self.total_updates if self.total_updates > 0 else 0.0
        q_states = len(self.Q)
        return {
            "q_table_states": q_states,
            "q_table_size": q_states,
            "current_connections": self.current_connections,
            "exploration_rate": round(self.exploration_rate, 4),
            "total_decisions": self.total_decisions,
            "total_updates": self.total_updates,
            "average_reward": round(avg_reward, 4),
            "total_reward": round(self._total_reward, 4),
            "positive_rewards": self._positive_rewards,
            "negative_rewards": self._negative_rewards,
            "throughput_improvements": self._throughput_improvements,
            "monitoring_interval": self.monitoring_interval,
        }

    def reset(self) -> None:
        """Clear all learned state."""
        self.Q.clear()
        self._last_state = None
        self._last_action = None
        self._last_metrics = None
        self._learn_pending = False
        self._last_learn_time = 0.0
        self._action_history.clear()
        self._metrics_history.clear()
        self.total_decisions = 0
        self.total_updates = 0
        self._total_reward = 0.0
        self._positive_rewards = 0
        self._negative_rewards = 0
        self._throughput_improvements = 0
        logger.info("RLAgent reset: Q-table cleared")