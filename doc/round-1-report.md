## 📋 第 1 轮报告 — Tushare 数据实现缺口分析

**轮次目标：** 对照用户列出的全部数据类别，逐项分析 Vibe-Trading 中 Tushare 相关实现的覆盖状态，识别缺口并给出实现建议。

### 执行概要
- 状态：**✅ 待审批**
- 分析范围：10 大数据类别，共约 30+ 个数据叶子项
- 分析依据：所有 Provider 层、Tool 层、MCP 层源码阅读 + 与 Tushare API 端点对照

### 架构方案

**影响范围：** 仅分析，不涉及代码修改。分析结果将指导后续实现轮次。

**分析维度：**
- Provider 层：`agent/backtest/loaders/tushare_*.py`
- Tool 层：`agent/src/tools/tushare_*_tool.py`
- MCP 层：`agent/mcp_server.py`
- 权限常量：`agent/backtest/loaders/_tushare_constants.py`

### 实现分析结果

#### 覆盖总览矩阵

| # | 数据类别 | 状态 | Provider | Tool/MCP | Tushare 端点 | 最低积分 |
|---|---|---|---|---|---|---|
| 1 | **股票列表** | ✅ 完整 | `tushare_refdata.fetch_stock_list()` | `search_security_master(kind="stock")` | `stock_basic` | 2000 |
| 2 | **ETF列表** | ✅ 完整 | `tushare_refdata.fetch_fund_list()` | `search_security_master(kind="fund")` | `fund_basic` | 2000 |
| 3 | **期权列表** | ✅ 完整 | `tushare_refdata.fetch_option_list()` | `search_security_master(kind="option")` | `opt_basic` | 5000 |
| 4 | **低频行情(日/周/月)** | ⚠️ 部分 | `tushare.DataLoader` 支持 daily/weekly/monthly + 指数/ETF/港股路由 | `get_market_data(source="tushare")` | `daily/weekly/monthly/index_daily/fund_daily/hk_daily` | 2000 |
| 5 | **财务数据** | ⚠️ 部分 | `tushare_fundamentals` 支持 A 股四表(资产负债表/利润表/现金流/财务指标) | `get_financial_statements(source="tushare")` | `balancesheet/income/cashflow/fina_indicator` | 2000 |
| 6 | **宏观经济** | ⚠️ 部分 | `tushare_featured.fetch_cn_macro()` 支持 GDP/CPI/PPI/Shibor/PMI/货币供应/社融/LPR | `get_featured_data(kind="cn_macro")` MCP 缺 start_month/end_month | `cn_gdp/cn_cpi/cn_ppi/shibor/cn_pmi/cn_m/sf_month/shibor_lpr` | 120~2000 |
| 7 | **ST股票列表** | ✅ 完整 | `tushare_featured.fetch_stock_st()` | `get_featured_data(kind="stock_st")` | `stock_st` | 3000 |
| 8 | **沪港通股票列表** | ✅ 完整 | `tushare_featured.fetch_hsgt_stocks()` | `get_featured_data(kind="hsgt_stocks")` | `stock_hsgt` | 3000 |
| 9 | **参考数据(六类)** | ⚠️ 部分 | Provider 全；Tool 有断层 | 详见下方明细 | 详见下方 | 120~5000 |
| 10 | **特色/打板数据** | ⚠️ 部分 | Provider 覆盖 16/22 个打板端点；Tool/MCP 有断层 | 详见下方明细 | 详见下方 | 2000~10000 |

#### 参考数据明细

| 数据项 | Provider / API | Tool / MCP | 积分 | 状态 |
|---|---|---|---|---|
| 质押 | `fetch_pledge_stat/detail` → `pledge_stat/pledge_detail` | ✅ `get_featured_data` | 500 | ✅ |
| 解禁 | `fetch_share_float` → `share_float` | ✅ `get_featured_data` | 120 | ✅ |
| 回购 | `fetch_repurchase` → `repurchase` | ✅ `get_featured_data` | 600 | ✅ |
| 增减持 | `fetch_holdertrade` → `stk_holdertrade` | ✅ `get_featured_data` | 2000 | ✅ |
| 龙虎榜 | `fetch_top_list` → `top_list` | ⚠️ `top_list` 未进入 Tool `_VALID_KINDS` | 2000 | ⚠️ |
| 融资融券 | `fetch_margin_detail` → `margin_detail` | ⚠️ `margin_detail` 未进入 Tool `_VALID_KINDS` | 2000 | ⚠️ |

#### 特色/打板数据明细

| 用户需求 | Tushare 端点 | 状态 | 积分 | 说明 |
|---|---|---|---|---|
| 概念板块及成分 | `ths_index/ths_member/ths_daily` | ✅ | 6000 | 完整 |
| 资金流向 | `moneyflow` | ⚠️ Provider 有，Tool/MCP 无 | 2000 | 需在 `_VALID_KINDS` 中增加 |
| 券商金股 | `broker_recommend` | ✅ | 6000 | 完整 |
| 筹码分布/胜率 | `cyq_chips/cyq_perf` | ✅ | 5000 | 完整 |
| 龙虎榜 | `top_list/top_inst` | ⚠️ `top_list` 未暴露 | 2000/5000 | `top_inst` 已可用 |
| 量化因子 | `stk_factor_pro/idx_factor_pro/fund_factor_pro` | ✅ | 5000 | 完整 |
| 盈利预测 | `report_rc` → fallback `forecast` | ✅ | 8000/2000 | 完整 |
| 机构调研 | `stk_surv` | ✅ | 5000 | 完整 |
| 游资数据 | `hm_list/hm_detail` | ✅ | 5000/10000 | 完整 |
| 涨跌停榜单 | `limit_list_d/limit_list_ths/limit_step` | ✅ | 5000/8000/8000 | 完整 |
| 热榜与热点板块 | `ths_hot/dc_hot/kpl_list/kpl_concept_cons/dc_daily` | ✅ | 6000~8000 | 完整 |

#### 缺失的 Tushare 打板专题端点（Provider 层缺口）

按 Tushare 官方目录，当前 Provider 层覆盖 16/22 个打板专题端点，缺失 6 个：

| 缺失端点 | 说明 | 最低积分 | 关联用户需求 |
|---|---|---|---|
| `dc_index` | 东方财富概念板块 | 6000 | 概念板块和成分 |
| `dc_member` | 东方财富板块成分 | 6000 | 概念板块和成分 |
| `tdx_index` | 通达信板块信息 | 6000 | 概念板块和成分 |
| `tdx_member` | 通达信板块成分 | 6000 | 概念板块和成分 |
| `tdx_daily` | 通达信板块行情 | 6000 | 个股及行业热板 |
| `limit_cpt_list` | 涨停最强板块统计 | 8000 | 行业热板/最强涨停板块 |

### 审查发现的问题

1. **Tool `_VALID_KINDS` 不全**：`tushare_featured_tool.py` 中 `_VALID_KINDS` 缺少 3 个已有 Provider 的 kind（`moneyflow`、`top_list`、`margin_detail`），导致 Agent 无法调用这些数据。

2. **MCP 参数签名陈旧**：`mcp_server.py` 中 `get_featured_data` 的 MCP 暴露仍沿用旧版 16 类参数模型，未同步最新 34 个 kind 和参数。

3. **港股财务数据路由缺失**：`tushare_fundamentals.py` 仅覆盖 A 股四表，港股应路由到 `hk_balancesheet/hk_income/hk_cashflow/hk_fina_indicator`。

4. **权限常量不完整**：`_tushare_constants.py` 的 `TUSHARE_PRIVILEGE_MAP` 缺少 `top_inst`、`limit_list_ths`、`limit_step`、`stk_factor_pro`、`idx_factor_pro`、`fund_factor_pro`、`hm_list`、`hm_detail`、`dc_hot`、`dc_daily`、`kpl_list` 等端点的积分映射。

5. **能力声明不一致**：Tool 注释称 32 类，实际 `_VALID_KINDS` 为 34 类；MCP 文档仍称 16 类。

6. **`hsgt_stocks` 端点名不一致**：信封中 `endpoint` 为 `hsgt_stocks`，但实际 Tushare API 名叫 `stock_hsgt`。

### 裁决建议

- **建议动作：** ✅ **批准进入下一步实现轮**
- **优先级建议：**
  - **P0（高优先级，简单修复）：** 在 `_VALID_KINDS` 中补充 `moneyflow`/`top_list`/`margin_detail`，同步 MCP 参数签名
  - **P1（中优先级）：** 补齐 6 个缺失的 Provider 端点 + Tool 接入
  - **P2（低优先级）：** 港股财务路由、权限常量补全、能力声明统一

- **风险提示：**
  - Tushare 积分和独立权限会调整，上线前应以账户权限页为准
  - "有 Provider"不等于 Agent 或 MCP 能调用——当前存在两者断层
  - Eastmoney 免费替代接口存在 IP 限流，适合降级但不能依赖

---

👤 **请审核本轮分析结果，批准后进入下一轮实现。**
