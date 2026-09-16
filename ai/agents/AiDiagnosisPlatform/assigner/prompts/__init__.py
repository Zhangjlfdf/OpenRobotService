"""派单 Prompt 唯一出口。审视清单见同目录 README.md。"""

from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import (
    dept_common_guardrails,
    dept_product_map,
    engineer_brief_lines,
    feature_role_routing_guidance,
    ticket_fields_block,
    ticket_type_guidance,
    ticket_type_key,
)
from ai.agents.AiDiagnosisPlatform.assigner.prompts.step0 import (
    build_collision,
    build_weak,
)
from ai.agents.AiDiagnosisPlatform.assigner.prompts.step1 import (
    build_audit,
    build_r2,
)
from ai.agents.AiDiagnosisPlatform.assigner.prompts.step3 import build_l1
from ai.agents.AiDiagnosisPlatform.assigner.prompts.step6 import (
    FEATURE_ROLE_ROUTING,
    IRON_RULES,
    JUDGE_HINTS,
    OUTPUT_CONTRACT,
)

__all__ = [
    "ticket_type_key",
    "ticket_type_guidance",
    "dept_product_map",
    "dept_common_guardrails",
    "ticket_fields_block",
    "engineer_brief_lines",
    "feature_role_routing_guidance",
    "build_weak",
    "build_collision",
    "build_r2",
    "build_audit",
    "build_l1",
    "FEATURE_ROLE_ROUTING",
    "IRON_RULES",
    "JUDGE_HINTS",
    "OUTPUT_CONTRACT",
]
