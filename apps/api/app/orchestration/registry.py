from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import AgentName
from app.agents.earnings_agent import EarningsManagementAgent
from app.agents.entity_agent import EntityIntelligenceAgent
from app.agents.filings_agent import FilingsGovernanceAgent
from app.agents.financials_agent import FinancialForensicAgent
from app.agents.industry_agent import IndustryPeerAgent
from app.agents.macro_agent import IndiaMacroPolicyFlowAgent
from app.agents.market_agent import LiveMarketAgent
from app.agents.news_agent import NewsEventAgent
from app.agents.risk_agent import RiskRedFlagAgent
from app.agents.sentiment_agent import SentimentNarrativeAgent
from app.agents.synthesis_agent import ChiefAnalystAgent
from app.agents.technical_agent import TechnicalDerivativesAgent
from app.agents.validator_agent import EvidenceCrossValidationAgent
from app.agents.valuation_agent import ValuationScenarioAgent
from app.agents.web_agent import WebIntelligenceAgent
from app.orchestration.runtime import AgentRegistry
from app.securities.repository import SecurityMasterRepository


def build_agent_registry(engine: AsyncEngine) -> AgentRegistry:
    """Build the complete runtime registry.

    Agent 1 is the OrchestratorRuntime itself; the remaining 15 logical roles are
    registered as handlers. This keeps orchestration deterministic rather than
    pretending the scheduler is another LLM call.
    """

    return AgentRegistry(
        {
            AgentName.ENTITY: EntityIntelligenceAgent(SecurityMasterRepository(engine)),
            AgentName.MARKET: LiveMarketAgent(),
            AgentName.FINANCIALS: FinancialForensicAgent(),
            AgentName.FILINGS: FilingsGovernanceAgent(),
            AgentName.EARNINGS: EarningsManagementAgent(),
            AgentName.NEWS: NewsEventAgent(),
            AgentName.WEB: WebIntelligenceAgent(),
            AgentName.INDUSTRY: IndustryPeerAgent(),
            AgentName.MACRO: IndiaMacroPolicyFlowAgent(),
            AgentName.VALUATION: ValuationScenarioAgent(),
            AgentName.TECHNICAL: TechnicalDerivativesAgent(),
            AgentName.SENTIMENT: SentimentNarrativeAgent(),
            AgentName.RISK: RiskRedFlagAgent(),
            AgentName.VALIDATOR: EvidenceCrossValidationAgent(),
            AgentName.SYNTHESIS: ChiefAnalystAgent(),
        }
    )
