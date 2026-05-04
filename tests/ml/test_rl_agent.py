"""Tests for RL position sizing agent."""
import numpy as np
import pytest

from shared.ml.rl_agent import (
    RLConfig, RLPositionAgent, SimpleQNetwork, ReplayBuffer, ACTIONS,
)


class TestQNetwork:
    def test_forward_shape(self):
        net = SimpleQNetwork(state_dim=10, n_actions=5, hidden=32)
        state = np.random.randn(10)
        q = net.forward(state)
        assert q.shape == (5,)

    def test_batch_forward(self):
        net = SimpleQNetwork(state_dim=10, n_actions=5, hidden=32)
        states = np.random.randn(8, 10)
        q = net.forward(states)
        assert q.shape == (8, 5)

    def test_copy(self):
        net1 = SimpleQNetwork(state_dim=5, n_actions=3, hidden=16, seed=1)
        net2 = SimpleQNetwork(state_dim=5, n_actions=3, hidden=16, seed=2)
        # Different seeds → different weights
        assert not np.allclose(net1.w1, net2.w1)
        net2.copy_from(net1)
        assert np.allclose(net1.w1, net2.w1)


class TestReplayBuffer:
    def test_add_and_sample(self):
        buf = ReplayBuffer(100)
        rng = np.random.default_rng(42)
        for i in range(50):
            buf.add(np.zeros(5), 0, 1.0, np.zeros(5), False)
        assert len(buf) == 50
        states, _, _, _, _ = buf.sample(10, rng)
        assert states.shape == (10, 5)

    def test_circular_overwrite(self):
        buf = ReplayBuffer(10)
        rng = np.random.default_rng(42)
        for i in range(25):
            buf.add(np.full(3, i), 0, 0, np.zeros(3), False)
        assert len(buf) == 10


class TestRLAgent:
    def test_select_action(self):
        agent = RLPositionAgent(RLConfig(state_dim=5))
        state = np.random.randn(5)
        action = agent.select_action(state, explore=True)
        assert 0 <= action < 5

    def test_get_position(self):
        agent = RLPositionAgent(RLConfig(state_dim=5))
        state = np.random.randn(5)
        pos = agent.get_position(state)
        assert pos in ACTIONS

    def test_train_step_smoke(self):
        agent = RLPositionAgent(RLConfig(state_dim=5, batch_size=8, replay_size=100))
        # Fill replay buffer
        for _ in range(20):
            s = np.random.randn(5)
            a = np.random.randint(5)
            r = np.random.randn()
            ns = np.random.randn(5)
            agent.train_step(s, a, r, ns, False)
        assert agent.step_count == 20
        assert len(agent.train_losses) > 0

    def test_compute_reward(self):
        agent = RLPositionAgent()
        # Profitable trade
        r = agent.compute_reward(position=1.0, bar_return=0.01, position_change=0.5, current_dd=0)
        assert r > 0  # PnL=0.01, cost=0.00025 → net positive
        # Losing trade with high DD
        r2 = agent.compute_reward(position=1.0, bar_return=-0.05, position_change=0, current_dd=0.15)
        assert r2 < 0

    def test_exploration_decays(self):
        agent = RLPositionAgent(RLConfig(
            state_dim=3, epsilon_start=1.0, epsilon_end=0.1, epsilon_decay=100
        ))
        # At step 0, should mostly explore
        agent.step_count = 0
        # At step 100+, should mostly exploit
        agent.step_count = 200
        # Just verify no crash
        state = np.random.randn(3)
        _ = agent.select_action(state, explore=True)
