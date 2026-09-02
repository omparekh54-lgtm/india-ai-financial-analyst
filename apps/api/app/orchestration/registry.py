from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import AgentName
from app.agents.earnings_agent import EarningsManagementAgent
from app.agents.entity_agent import EntityIntelligenceAgent
from app.agents.filings_agent import FilingsGovernanceAgent
from app.agents.financials_agent import FinancialForensicAgent
from app.agents.industry_agent import IndustryPeerAgent
from app.agents.llm_enrichment import LlmEnrichedAgent, LlmSynthesisAgent
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
from app.core.config import Settings, get_settings
from app.orchestration.runtime import AgentRegistry
from app.providers.gateway import ProviderGateway
from app.providers.router import Capability
from app.securities.repository import SecurityMasterRepository


def build_agent_registry(
    engine: AsyncEngine,
    settings: Settings | None = None,
) -> AgentRegistry:
    """Build the complete runtime registry.

    Agent 1 is the deterministic OrchestratorRuntime. Calculation-heavy agents remain
    code-first. Qualitative agents can receive a bounded LLM enrichment pass when the
    external-LLM runtime flag is enabled; all added claims still pass Agent 15.
    """

    runtime_settings = settings or get_settings()
    gateway = ProviderGateway(runtime_settings)

    return AgentRegistry(
        {
            AgentName.ENTITY: EntityIntelligenceAgent(SecurityMasterRepository(engine)),
            AgentName.MARKET: LiveMarketAgent(),
            AgentName.FINANCIALS: FinancialForensicAgent(),
            AgentName.FILINGS: LlmEnrichedAgent(
                FilingsGovernanceAgent(),
                agent=AgentName.FILINGS,
                gateway=gateway,
                capability=Capability.DEEP_REASONING,
                max_evidence=8,
            ),
            AgentName.EARNINGS: LlmEnrichedAgent(
                EarningsManagementAgent(),
                agent=AgentName.EARNINGS,
                gateway=gateway,
                capability=Capability.LONG_CONTEXT,
                max_evidence=10,
            ),
            AgentName.NEWS: LlmEnrichedAgent(
                NewsEventAgent(),
                agent=AgentName.NEWS,
                gateway=gateway,
                capability=Capability.FAST_REASONING,
                max_evidence=8,
            ),
            AgentName.WEB: LlmEnrichedAgent(
                WebIntelligenceAgent(),
                agent=AgentName.WEB,
                gateway=gateway,
                capability=Capability.FAST_REASONING,
                max_evidence=8,
            ),
            AgentName.INDUSTRY: LlmEnrichedAgent(
                IndustryPeerAgent(),
                agent=AgentName.INDUSTRY,
                gateway=gateway,
                capability=Capability.LOW_LATENCY,
                max_evidence=8,
            ),
            AgentName.MACRO: IndiaMacroPolicyFlowAgent(),
            AgentName.VALUATION: ValuationScenarioAgent(),
            AgentName.TECHNICAL: TechnicalDerivativesAgent(),
            AgentName.SENTIMENT: LlmEnrichedAgent(
                SentimentNarrativeAgent(),
                agent=AgentName.SENTIMENT,
                gateway=gateway,
                capability=Capability.FAST_REASONING,
                max_evidence=8,
            ),
            AgentName.RISK: LlmEnrichedAgent(
                RiskRedFlagAgent(),
                agent=AgentName.RISK,
                gateway=gateway,
                capability=Capability.DEEP_REASONING,
                max_evidence=10,
            ),
            AgentName.VALIDATOR: EvidenceCrossValidationAgent(),
            AgentName.SYNTHESIS: LlmSynthesisAgent(ChiefAnalystAgent(), gateway),
        }
    )
