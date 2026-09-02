from __future__ import annotations

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.securities.repository import SecurityMasterRepository
from app.securities.resolver import SecurityResolver


class EntityIntelligenceAgent:
    def __init__(self, repository: SecurityMasterRepository) -> None:
        self.repository = repository

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        securities = await self.repository.list_all()
        result = SecurityResolver(securities).resolve(agent_input.query)

        if not result.resolved or result.candidate is None:
            return AgentOutput(
                agent=AgentName.ENTITY,
                ok=False,
                warnings=["Security could not be resolved with sufficient confidence"],
                metrics={"alternatives": [candidate.model_dump() for candidate in result.alternatives]},
            )

        security = result.candidate.security
        statement = f"Resolved request to {security.legal_name}"
        return AgentOutput(
            agent=AgentName.ENTITY,
            claims=[
                Claim(
                    agent=AgentName.ENTITY,
                    statement=statement,
                    claim_type="fact",
                    confidence=result.candidate.score,
                    status="verified",
                    data=security.model_dump(),
                )
            ],
            metrics={
                "resolution_score": result.candidate.score,
                "match_reason": result.candidate.match_reason,
                "security": security.model_dump(),
            },
        )
