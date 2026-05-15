"""Reinforcement Learning agent for position sizing optimization.

A simple DQN-style agent that learns optimal position sizes given
market state features. Uses pure numpy (no PyTorch/TF dependency).

Architecture:
  State: [alpha_signals, vol_regime, drawdown, current_position, features_top10]
  Actions: [-1.0, -0.5, 0.0, 0.5, 1.0] (discrete position targets)
  Reward: realized PnL - cost - DD penalty

Why RL here (and not standalone):
  - RL is NOT for predicting direction (alphas do that)
  - RL optimizes HOW MUCH to trade given alpha signals + market state
  - This is a meta-level decision: "given alpha says +0.7, should I follow fully?"
  - Acts as a learned risk manager on top of existing alphas

Anti-overfit:
  - Simple architecture (2-layer net, 64 hidden)
  - Large replay buffer (10,000 transitions)
  - Epsilon-greedy exploration (decays from 1.0 to 0.1)
  - Walk-forward training only (no in-sample evaluation)
  - Reward includes cost and drawdown penalty
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class RLConfig:
    """RL agent configuration."""
    state_dim: int = 15           # input features
    n_actions: int = 5            # [-1, -0.5, 0, 0.5, 1]
    hidden_dim: int = 64
    learning_rate: float = 0.001
    gamma: float = 0.99           # discount factor
    epsilon_start: float = 1.0
    epsilon_end: float = 0.1
    epsilon_decay: int = 5000     # steps to decay
    replay_size: int = 10000
    batch_size: int = 64
    target_update_freq: int = 100
    cost_bps: float = 5.0
    dd_penalty: float = 0.5      # penalty multiplier for drawdown


# Action space
ACTIONS = np.array([-1.0, -0.5, 0.0, 0.5, 1.0])


class SimpleQNetwork:
    """2-layer fully-connected Q-network in pure numpy."""

    def __init__(self, state_dim: int, n_actions: int, hidden: int, seed: int = 42):
        rng = np.random.default_rng(seed)
        scale1 = np.sqrt(2.0 / state_dim)
        scale2 = np.sqrt(2.0 / hidden)
        self.w1 = rng.normal(0, scale1, (state_dim, hidden))
        self.b1 = np.zeros(hidden)
        self.w2 = rng.normal(0, scale2, (hidden, n_actions))
        self.b2 = np.zeros(n_actions)

    def forward(self, state: np.ndarray) -> np.ndarray:
        """Forward pass: state → Q-values for each action."""
        h = np.maximum(0, state @ self.w1 + self.b1)  # ReLU
        q = h @ self.w2 + self.b2
        return q

    def copy_from(self, other: "SimpleQNetwork"):
        self.w1 = other.w1.copy()
        self.b1 = other.b1.copy()
        self.w2 = other.w2.copy()
        self.b2 = other.b2.copy()


class ReplayBuffer:
    """Fixed-size circular replay buffer."""

    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self.buffer: list[tuple] = []
        self.idx = 0

    def add(self, state, action_idx, reward, next_state, done):
        entry = (state.copy(), action_idx, reward, next_state.copy(), done)
        if len(self.buffer) < self.max_size:
            self.buffer.append(entry)
        else:
            self.buffer[self.idx % self.max_size] = entry
        self.idx += 1

    def sample(self, batch_size: int, rng: np.random.Generator):
        indices = rng.choice(len(self.buffer), size=min(batch_size, len(self.buffer)), replace=False)
        batch = [self.buffer[i] for i in indices]
        states = np.array([b[0] for b in batch])
        actions = np.array([b[1] for b in batch])
        rewards = np.array([b[2] for b in batch])
        next_states = np.array([b[3] for b in batch])
        dones = np.array([b[4] for b in batch])
        return states, actions, rewards, next_states, dones

    def __len__(self):
        return len(self.buffer)


class RLPositionAgent:
    """DQN agent for position sizing."""

    def __init__(self, config: RLConfig | None = None):
        self.config = config or RLConfig()
        c = self.config
        self.q_net = SimpleQNetwork(c.state_dim, c.n_actions, c.hidden_dim, seed=42)
        self.target_net = SimpleQNetwork(c.state_dim, c.n_actions, c.hidden_dim, seed=42)
        self.target_net.copy_from(self.q_net)
        self.replay = ReplayBuffer(c.replay_size)
        self.rng = np.random.default_rng(42)
        self.step_count = 0
        self.train_losses: list[float] = []

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        """Select action index using epsilon-greedy."""
        c = self.config
        epsilon = c.epsilon_end + (c.epsilon_start - c.epsilon_end) * \
                  max(0, 1 - self.step_count / c.epsilon_decay)

        if explore and self.rng.random() < epsilon:
            return int(self.rng.choice(c.n_actions))

        q_values = self.q_net.forward(state)
        return int(np.argmax(q_values))

    def get_position(self, state: np.ndarray, explore: bool = False) -> float:
        """Get position target from state."""
        action_idx = self.select_action(state, explore=explore)
        return float(ACTIONS[action_idx])

    def train_step(self, state, action_idx, reward, next_state, done):
        """Store transition and train if enough data."""
        self.replay.add(state, action_idx, reward, next_state, done)
        self.step_count += 1

        if len(self.replay) < self.config.batch_size * 2:
            return

        # Sample batch
        states, actions, rewards, next_states, dones = \
            self.replay.sample(self.config.batch_size, self.rng)

        # Compute targets (DQN)
        c = self.config
        next_q = self.target_net.forward(next_states)
        max_next_q = np.max(next_q, axis=1)
        targets = rewards + c.gamma * max_next_q * (1 - dones)

        # Current Q-values
        current_q = self.q_net.forward(states)
        # Extract Q-values for taken actions
        q_taken = current_q[np.arange(len(actions)), actions.astype(int)]

        # TD error
        td_error = targets - q_taken
        loss = float(np.mean(td_error ** 2))
        self.train_losses.append(loss)

        # Simple gradient descent on Q-network
        # (manual backprop through 2-layer network)
        lr = c.learning_rate
        batch_size = len(states)

        # Forward pass (recompute intermediates)
        h = np.maximum(0, states @ self.q_net.w1 + self.q_net.b1)

        # Gradient of loss w.r.t. Q-values (only for taken actions)
        dq = np.zeros_like(current_q)
        dq[np.arange(batch_size), actions.astype(int)] = -2 * td_error / batch_size

        # Backprop through layer 2
        dw2 = h.T @ dq
        db2 = dq.sum(axis=0)

        # Backprop through ReLU + layer 1
        dh = dq @ self.q_net.w2.T
        dh[h <= 0] = 0  # ReLU gradient
        dw1 = states.T @ dh
        db1 = dh.sum(axis=0)

        # Gradient clipping
        for grad in [dw1, db1, dw2, db2]:
            np.clip(grad, -1.0, 1.0, out=grad)

        # Update
        self.q_net.w1 -= lr * dw1
        self.q_net.b1 -= lr * db1
        self.q_net.w2 -= lr * dw2
        self.q_net.b2 -= lr * db2

        # Update target network periodically
        if self.step_count % c.target_update_freq == 0:
            self.target_net.copy_from(self.q_net)

    def compute_reward(
        self,
        position: float,
        bar_return: float,
        position_change: float,
        current_dd: float,
    ) -> float:
        """Compute reward: PnL - costs - DD penalty."""
        c = self.config
        pnl = position * bar_return
        cost = abs(position_change) * c.cost_bps / 10_000
        dd_pen = c.dd_penalty * max(0, current_dd - 0.05)  # penalize DD > 5%
        return pnl - cost - dd_pen
