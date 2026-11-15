from typing import Any, Dict, Protocol

import numpy as np


class Agent(Protocol):
    def act(self, obs: Dict[str, Any]) -> np.ndarray:
        """Returns an action given an observation.

        Args:
            obs: observation from the environment.

        Returns:
            action: action to take on the environment.
        """
        raise NotImplementedError


class DummyAgent(Agent):
    def __init__(self, num_dofs: int):
        self.num_dofs = num_dofs

    def act(self, obs: Dict[str, Any]) -> np.ndarray:
        return np.zeros(self.num_dofs)


class BimanualAgent(Agent):
    def __init__(self, agent_left: Agent, agent_right: Agent):
        self.agent_left = agent_left
        self.agent_right = agent_right

    def act(self, obs: Dict[str, Any]) -> np.ndarray:
        left_obs = {}
        right_obs = {}
        for key, val in obs.items():
            L = val.shape[0]
            half_dim = L // 2
            assert L == half_dim * 2, f"{key} must be even, something is wrong"
            left_obs[key] = val[:half_dim]
            right_obs[key] = val[half_dim:]
        return np.concatenate(
            [self.agent_left.act(left_obs), self.agent_right.act(right_obs)]
        )


class MultiArmAgent(Agent):
    """Split observations evenly and concatenate commands for named arms."""

    def __init__(self, agents: dict[str, Agent]):
        if not agents:
            raise ValueError("at least one agent is required")
        self.agents = dict(agents)

    def act(self, obs: Dict[str, Any]) -> np.ndarray:
        count = len(self.agents)
        per_arm = {name: {} for name in self.agents}
        for key, value in obs.items():
            if value.shape[0] % count:
                raise ValueError(f"{key} cannot be split across {count} arms")
            chunks = np.split(value, count)
            for name, chunk in zip(self.agents, chunks, strict=True):
                per_arm[name][key] = chunk
        return np.concatenate(
            [agent.act(per_arm[name]) for name, agent in self.agents.items()]
        )
