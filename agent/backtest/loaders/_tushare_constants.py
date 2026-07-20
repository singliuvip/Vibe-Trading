"""Shared Tushare constants used across multiple loader/provider modules.

Extracted to avoid duplicate definitions of TUSHARE_TOKEN_PLACEHOLDERS
across tushare.py, tushare_fundamentals.py, tushare_realtime.py, and
tushare_auction.py.
"""

TUSHARE_TOKEN_PLACEHOLDERS = {"", "your-tushare-token"}

# Machine-readable privilege map: endpoint → minimum Tushare points
# Based on Tushare official documentation; verify before deployment.
# Endpoints not listed either have no published minimum or are free.
TUSHARE_PRIVILEGE_MAP: dict[str, int] = {
    "stock_basic": 2000,
    "fund_basic": 2000,
    "opt_basic": 5000,
    "daily": 2000,
    "weekly": 2000,
    "monthly": 2000,
    "balancesheet": 2000,
    "income": 2000,
    "cashflow": 2000,
    "fina_indicator": 2000,
    "cn_gdp": 600,
    "cn_cpi": 600,
    "cn_ppi": 600,
    "cn_m": 600,
    "shibor": 120,
    "shibor_lpr": 120,
    "cn_pmi": 2000,
    "sf_month": 2000,
    "stock_st": 3000,
    "stock_hsgt": 3000,
    "pledge_stat": 500,
    "share_float": 120,
    "repurchase": 600,
    "stk_holdertrade": 2000,
    "top_list": 2000,
    "top_inst": 5000,
    "margin_detail": 2000,
    "margin": 2000,
    "margin_secs": 2000,
    "moneyflow": 2000,
    "ths_index": 6000,
    "ths_member": 6000,
    "ths_daily": 6000,
    "broker_recommend": 6000,
    "cyq_perf": 5000,
    "cyq_chips": 5000,
    "stk_factor_pro": 5000,
    "idx_factor_pro": 5000,
    "fund_factor_pro": 5000,
    "report_rc": 8000,
    "forecast": 2000,
    "stk_surv": 5000,
    "hm_list": 5000,
    "hm_detail": 10000,
    "limit_list_d": 5000,
    "limit_list_ths": 8000,
    "limit_step": 8000,
    "ths_hot": 6000,
    "dc_hot": 8000,
    "dc_daily": 6000,
    "kpl_list": 5000,
    "kpl_concept_cons": 5000,
    "pledge_detail": 500,
    "dc_index": 6000,
    "dc_member": 6000,
    "tdx_index": 6000,
    "tdx_member": 6000,
    "tdx_daily": 6000,
    "limit_cpt_list": 8000,
}
