from abc import ABC, abstractmethod
from typing import List
from app.agents.base import Agent
from app.orchestration.state import DebateState


class BaseDebateStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        pass

    @abstractmethod
    def get_speakers_for_round(
        self, active_agents: List[Agent], round_num: int, state: DebateState
    ) -> List[Agent]:
        """Returns the list of specialist agents who should speak in the given round."""
        pass


class FreeDebateStrategy(BaseDebateStrategy):
    """
    자유 토론 전략:
    오케스트레이터를 제외한 모든 활성 전문가 에이전트가 유기적으로 발언합니다.
    """
    name = "free_debate"
    display_name = "자유 토론 (Free Debate)"

    def get_speakers_for_round(
        self, active_agents: List[Agent], round_num: int, state: DebateState
    ) -> List[Agent]:
        # Exclude orchestrator from intermediate speaker list (orchestrator moderates before/after)
        specialists = [a for a in active_agents if a.key != "orchestrator"]
        return specialists if specialists else active_agents


class SequentialReviewStrategy(BaseDebateStrategy):
    """
    순차 검증 전략:
    설계(Architect) -> 구현(Coder) -> 보안/품질 검증(Critic) 순서로 엄격히 순차 검토합니다.
    """
    name = "sequential_review"
    display_name = "순차 검증 (Sequential Review)"

    def get_speakers_for_round(
        self, active_agents: List[Agent], round_num: int, state: DebateState
    ) -> List[Agent]:
        specialists = [a for a in active_agents if a.key != "orchestrator"]
        # Order by standard priority: architect -> coder -> critic -> others
        order_priority = {"architect": 0, "coder": 1, "critic": 2}
        sorted_specialists = sorted(
            specialists, key=lambda a: order_priority.get(a.key, 10)
        )
        return sorted_specialists if sorted_specialists else active_agents


class AdversarialDebateStrategy(BaseDebateStrategy):
    """
    디베이트/대립 토론 전략:
    제안자(Architect/Coder)와 비판자(Critic) 간의 1:1 대립 피드백 토론.
    """
    name = "adversarial_debate"
    display_name = "디베이트 (Debate & Critique)"

    def get_speakers_for_round(
        self, active_agents: List[Agent], round_num: int, state: DebateState
    ) -> List[Agent]:
        specialists = [a for a in active_agents if a.key != "orchestrator"]
        proponents = [a for a in specialists if a.key in ["architect", "coder"]]
        critics = [a for a in specialists if a.key == "critic"]
        others = [a for a in specialists if a not in proponents and a not in critics]

        # Alternating order
        speakers: List[Agent] = []
        max_len = max(len(proponents), len(critics))
        for i in range(max_len):
            if i < len(proponents):
                speakers.append(proponents[i])
            if i < len(critics):
                speakers.append(critics[i])
        speakers.extend(others)
        return speakers if speakers else active_agents


STRATEGY_MAP = {
    "free_debate": FreeDebateStrategy(),
    "sequential_review": SequentialReviewStrategy(),
    "adversarial_debate": AdversarialDebateStrategy(),
}


def get_strategy(strategy_name: str) -> BaseDebateStrategy:
    return STRATEGY_MAP.get(strategy_name, STRATEGY_MAP["free_debate"])
