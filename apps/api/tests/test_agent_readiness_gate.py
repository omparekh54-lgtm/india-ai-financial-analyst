from scripts.run_agent_readiness_gate import _exit_code


def test_readiness_gate_requires_corpus_and_agents() -> None:
    assert _exit_code(corpus_ready=True, agents_ready=True) == 0
    assert _exit_code(corpus_ready=False, agents_ready=True) == 1
    assert _exit_code(corpus_ready=True, agents_ready=False) == 1
    assert _exit_code(corpus_ready=False, agents_ready=False) == 1
