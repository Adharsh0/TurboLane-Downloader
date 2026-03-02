
"""
adapter.py

TurboLaneAdapter — the ONLY bridge between the TurboLane engine and the
download manager application.
"""

import logging
from turbolane import TurboLaneEngine

logger = logging.getLogger(__name__)


class TurboLaneAdapter:

    def __init__(
        self,
        model_dir: str = "models/edge",
        min_connections: int = 1,
        max_connections: int = 16,
        default_connections: int = 8,
        monitoring_interval: float = 5.0,
    ):
        self._engine = TurboLaneEngine(
            mode="client",
            algorithm="qlearning",
            model_dir=model_dir,
            min_connections=min_connections,
            max_connections=max_connections,
            default_connections=default_connections,
            monitoring_interval=monitoring_interval,
            exploration_rate=0.2,        # lower initial exploration
            exploration_decay=0.98,      # decay faster
            min_exploration=0.05,
        )
        logger.info(
            "TurboLaneAdapter ready: connections=[%d..%d] default=%d",
            min_connections, max_connections, default_connections,
        )

    def decide(self, throughput_mbps, rtt_ms, loss_pct):
        return self._engine.decide(throughput_mbps, rtt_ms, loss_pct)

    def learn(self, throughput_mbps, rtt_ms, loss_pct):
        self._engine.learn(throughput_mbps, rtt_ms, loss_pct)

    def save(self):
        return self._engine.save()

    def get_stats(self):
        return self._engine.get_stats()

    def reset(self):
        self._engine.reset()

    @property
    def current_connections(self):
        return self._engine.current_connections


adapter = TurboLaneAdapter()