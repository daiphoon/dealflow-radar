"""跨抽取、验证、归并和展示共用的类型定义；候选规则与语义判断独立。"""

CONTRACT_VERSION = "matter-contract-v2"

LABELS = {
    "fund_commitment": "基金认缴",
    "outbound_investment": "对外投资",
    "acquisition_target": "被收购",
    "registered_capital": "注册资本变化",
    "ipo_guidance_agreement": "辅导协议签署",
    "company_financing": "获得融资",
    "ipo_guidance_completed": "辅导完成",
    "ipo_guidance": "辅导备案",
    "ipo_hearing": "上市聆讯",
    "ipo_application": "上市申请提交",
    "ipo_inquiry": "上市问询",
    "ipo_accepted": "上市申请受理",
    "ipo_filing": "境外上市备案",
    "ipo_offering": "招股发行",
    "ipo_listed": "挂牌上市",
    "ipo_listing_plan": "上市计划",
    "ipo_withdrawn": "上市申请撤回",
    "contract_award": "合同与中标",
    "product_milestone": "产品技术进展",
    "operating_disclosure": "经营披露",
    "management_change": "治理人员变化",
    "legal_development": "司法合规进展",
    "capacity_development": "产能资产进展",
}
STATUS_LABELS = {
    "denied": "否认或更正",
    "planned": "计划",
    "committed": "认缴承诺",
    "conditional": "附条件安排",
    "reported": "来源报道",
}
FIELD_LABELS = {
    "share_quantity": "股份数量",
    "valuation": "估值",
    "registered_capital_before": "变更前注册资本",
    "registered_capital_after": "变更后注册资本",
    "commitment": "认缴金额",
    "investment": "对外投资金额",
    "financing": "融资金额",
    "proposed_proceeds": "拟募集金额",
    "reported_amount": "来源金额（性质待核）",
    "round": "融资轮次",
    "investors": "投资方（来源口径）",
    "date": "原文日期",
    "occurred": "发生时间",
    "disclosed": "披露时间",
    "planned": "计划时间",
    "market": "上市市场",
    "application_cycle": "申请周期",
    "buyer": "收购方",
    "target": "收购标的",
    "acquisition_scope": "收购范围",
    "acquisition_stage": "收购进展",
    "transaction_id": "交易标识",
    "project_id": "项目标识",
}

CATEGORIES = {
    "fund_commitment": "financing_cap_table",
    "outbound_investment": "financing_cap_table",
    "acquisition_target": "exit_liquidity",
    "registered_capital": "financing_cap_table",
    "company_financing": "financing_cap_table",
    "ipo_guidance_agreement": "exit_liquidity",
    "ipo_guidance_completed": "exit_liquidity",
    "ipo_guidance": "exit_liquidity",
    "ipo_application": "exit_liquidity",
    "ipo_hearing": "exit_liquidity",
    "ipo_inquiry": "exit_liquidity",
    "ipo_accepted": "exit_liquidity",
    "ipo_filing": "exit_liquidity",
    "ipo_offering": "exit_liquidity",
    "ipo_listed": "exit_liquidity",
    "ipo_listing_plan": "exit_liquidity",
    "ipo_withdrawn": "exit_liquidity",
    "contract_award": "contract_commercial",
    "product_milestone": "product_technology",
    "operating_disclosure": "financial_operation",
    "management_change": "governance_people",
    "legal_development": "legal_compliance",
    "capacity_development": "capacity_assets",
}


def subject_role(subtype):
    if subtype == "company_financing":
        return "fundraiser"
    if subtype == "outbound_investment":
        return "buyer"
    if subtype == "acquisition_target":
        return "target"
    if subtype == "fund_commitment":
        return "investor"
    return "issuer" if subtype.startswith("ipo_") else "actor"
