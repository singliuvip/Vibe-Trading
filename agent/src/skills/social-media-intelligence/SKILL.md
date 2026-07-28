---
name: social-media-intelligence
description: "A股社媒情绪情报：东方财富股吧结构化采集 + 雪球/微博非结构化抓取 + LLM情绪量化 + 热度/看多比例/新人指数/KOL一致性信号构建，面向A股情绪驱动策略。"
category: tool
---

# A股社媒情绪情报（Social Media Intelligence）

> 本技能整合 A 股社媒生态的情绪采集与量化方法，覆盖东方财富股吧（结构化）、雪球/微博（非结构化），输出热度指数、看多比例、新人指数、KOL一致性等情绪信号。情绪打分由 `social_analyst` agent 调 LLM 完成（与 event-driven skill 的"采集原始数据、上游打分"范式一致），本技能只提供采集与聚合方法。

---

## 1. A股社媒情绪生态概述

A股散户占比高，社媒情绪对短期定价影响显著。核心阵地：

| 平台 | 结构化程度 | 内容特征 | 信号价值 |
|------|-----------|---------|---------|
| 东方财富股吧（guba） | 高（结构化帖子元数据） | 个股讨论最集中，散户情绪温度计 | 热度/看多比例/新人指数核心源 |
| 雪球 | 中（HTML/部分API） | 偏价值/趋势投资者，长帖多 | KOL一致性、机构观点扩散 |
| 微博 | 低（非结构化） | 财经大V + 散户短帖，传播快 | 事件驱动情绪脉冲 |
| 财经论坛（淘股吧/理想论坛） | 中 | 短线游资/打板客聚集 | 题材热度、龙头股情绪 |

**采集分工**：
- 股吧 → `get_a_share_social_sentiment` 工具（结构化帖子元数据，免鉴权）
- 雪球/微博/论坛 → `read_url` 工具（非结构化 HTML 抓取 + 解析）

---

## 2. 数据采集方法

### 2.1 东方财富股吧（结构化）

**工具**：`get_a_share_social_sentiment`（`src.tools.a_share_social_sentiment_tool.AShareSocialSentimentTool`）

免鉴权、按源 IP 限流（经 `eastmoney` 节流 bucket）。返回个股股吧帖子元数据，**不含情绪打分**。

```python
from src.tools.a_share_social_sentiment_tool import AShareSocialSentimentTool

# 茅台股吧最近 50 条帖子元数据
print(AShareSocialSentimentTool().execute(code="600519.SH", limit=50))
```

**返回信封**：
```json
{
  "ok": true,
  "market": "a_share",
  "source": "guba",
  "data": {
    "code": "600519.SH",
    "secid": "1.600519",
    "posts": [
      {"title", "author", "published", "read_count", "comment_count", "url"}
    ]
  }
}
```

**字段说明**：

| 字段 | 描述 |
|------|------|
| title | 帖子标题（裁剪至120字符） |
| author | 发帖人昵称 |
| published | 发布时间 |
| read_count | 阅读数（热度代理） |
| comment_count | 评论数（参与度代理） |
| url | 帖子链接 |

**采集频率建议**：
- 财报季/重大事件：每30分钟
- 常规监控：每小时
- 历史回填：每日批量

### 2.2 雪球（非结构化）

**工具**：`read_url`（抓取个股雪球专页 HTML，解析帖子列表）

雪球无稳定公开 JSON 端点，用 `read_url` 抓取 `https://xueqiu.com/S/<code>` 页面，正则解析帖子标题/作者/互动数。

```python
# 伪代码：雪球专页抓取 + 解析
from src.tools.web_reader_tool import WebReaderTool
import re

html = WebReaderTool().execute(url="https://xueqiu.com/S/SH600519")
# 解析逻辑由 social_analyst agent 完成
```

**数据 schema（雪球）**：
```json
{
  "platform": "xueqiu",
  "collected_at": "2026-07-28T08:00:00Z",
  "code": "600519.SH",
  "items": [
    {"title", "author", "published", "reply_count", "retweet_count", "like_count", "url"}
  ]
}
```

### 2.3 微博（非结构化）

**工具**：`read_url`（抓取财经话题页/个股超话）

微博财经话题（如 `#A股#`、`#茅台#`）和个股超话是散户情绪脉冲源。用 `read_url` 抓取话题页，解析短帖文本/转发数/评论数。

```python
# 伪代码：微博话题抓取
html = WebReaderTool().execute(url="https://weibo.com/search?q=%23%E8%8C%85%E5%8F%B0%23")
```

**数据 schema（微博）**：
```json
{
  "platform": "weibo",
  "collected_at": "2026-07-28T08:00:00Z",
  "query": "#茅台#",
  "items": [
    {"text", "author", "published", "repost_count", "comment_count", "like_count"}
  ]
}
```

### 2.4 合规与隐私

- 股吧/雪球/微博均为公开页面，采集公开帖子元数据
- 用户ID以掩码形式存储（如哈希），不保留原始用户名
- 原始文本本地存储，不通过公开API暴露
- 定期清理30天以上原始数据，仅保留聚合指标

---

## 3. 情绪量化方法

### 3.1 单条文本情绪打分

#### 方案A：LLM打分（首选，精度最高）

输出 `-1.0..1.0`，与 event-driven skill 一致。适合中文财经语境（股吧/雪球/微博均为中文，VADER/FinBERT 英文模型不适用）。

```python
def llm_sentiment(text: str, code: str | None = None) -> dict:
    """用 LLM 分析中文财经文本情绪。

    Args:
        text: 帖子标题/正文
        code: 关联个股代码

    Returns:
        {'score': float[-1.0,1.0], 'label': 'bullish'|'bearish'|'neutral', 'reason': str}
    """
    context = f"个股: {code}\n" if code else ""
    prompt = f"""{context}分析以下中文财经社媒文本的情绪，返回JSON：
{{"score": <-1.0到1.0的浮点>, "label": <"bullish"|"bearish"|"neutral">, "reason": <一句话解释>}}

文本: {text[:500]}"""
    from src.providers.base import get_llm
    response = get_llm().invoke(prompt)
    import json
    return json.loads(response.content)
```

**评分尺度**：
| score | label | 含义 |
|-------|-------|------|
| > 0.5 | bullish | 强烈看多 |
| 0.1 ~ 0.5 | bullish | 偏多 |
| -0.1 ~ 0.1 | neutral | 中性 |
| -0.5 ~ -0.1 | bearish | 偏空 |
| < -0.5 | bearish | 强烈看空 |

#### 方案B：规则词典兜底（LLM不可用时）

中文财经情绪词典，快速批量打分：

```python
_BULLISH_WORDS = {"涨停", "牛市", "利好", "突破", "加仓", "抄底", "龙头", "主升", "放量上涨"}
_BEARISH_WORDS = {"跌停", "崩盘", "利空", "破位", "割肉", "清仓", "暴雷", "跳水", "放量下跌"}

def lexicon_score(text: str) -> float:
    """规则词典打分，输出 -1.0..1.0。

    Args:
        text: 帖子文本

    Returns:
        情绪分：正向词命中数 - 负向词命中数，归一化到 [-1, 1]。
        无命中返回 0.0。
    """
    bull = sum(1 for w in _BULLISH_WORDS if w in text)
    bear = sum(1 for w in _BEARISH_WORDS if w in text)
    total = bull + bear
    if total == 0:
        return 0.0
    return (bull - bear) / total
```

**场景推荐**：
| 场景 | 推荐方案 | 原因 |
|------|---------|------|
| 股吧/雪球批量打分 | LLM | 中文财经语境理解最佳 |
| 实时高频打分 | 规则词典 | 低延迟，无API成本 |
| 微博事件脉冲 | LLM | 短文本+反讽多，需语义理解 |
| 词典置信度低时 | LLM兜底 | 复杂语义需LLM |

### 3.2 讨论热度指标

```python
import pandas as pd
import numpy as np

def compute_buzz_metrics(df: pd.DataFrame, window: str = "1H") -> pd.DataFrame:
    """计算时序讨论热度指标。

    Args:
        df: 帖子DataFrame，含 timestamp / platform / code 列
        window: 聚合窗口，如 "1H" / "4H" / "1D"

    Returns:
        时序DataFrame：msg_count / unique_authors / topic_freq / buzz_zscore
    """
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()

    result = df.resample(window).agg(
        msg_count=("title", "count"),
        unique_authors=("author", "nunique"),
        total_engagement=("read_count", "sum"),
    )

    total = df.resample(window)["title"].count()
    result["topic_freq"] = result["msg_count"] / total.replace(0, np.nan)

    roll_mean = result["msg_count"].rolling(30, min_periods=5).mean()
    roll_std = result["msg_count"].rolling(30, min_periods=5).std()
    result["buzz_zscore"] = (result["msg_count"] - roll_mean) / roll_std.replace(0, np.nan)

    return result
```

**关键热度指标**：
- `msg_count`：帖子数，绝对关注度
- `unique_authors`：独立发帖人，降低机器人/营销号干扰
- `buzz_zscore > 2.0`：异常热度，触发预警
- `topic_freq`：相对热度，控制全市场情绪放大

### 3.3 情绪极值（恐贪指标）

```python
def compute_fear_greed_index(
    sentiment_series: pd.Series,
    buzz_series: pd.Series,
    lookback: int = 30,
) -> pd.Series:
    """构建A股恐贪指数（社媒维度）。

    Args:
        sentiment_series: 日均情绪分 [-1, 1]
        buzz_series: 日帖子量序列
        lookback: 百分位排名历史窗口（天）

    Returns:
        恐贪指数 [0, 100]
        0-20 极度恐惧 / 20-40 恐惧 / 40-60 中性 / 60-80 贪婪 / 80-100 极度贪婪
    """
    sentiment_rank = sentiment_series.rolling(lookback).rank(pct=True) * 100
    buzz_rank = buzz_series.rolling(lookback).rank(pct=True) * 100
    fear_greed = 0.6 * sentiment_rank + 0.4 * buzz_rank
    return fear_greed.clip(0, 100)
```

**极值阈值**：
| 区间 | 状态 | 历史含义 | 交易解读 |
|------|------|---------|---------|
| 0-20 | 极度恐惧 | 恐慌抛售、流动性压力 | 逆向做多候选 |
| 20-40 | 恐惧 | 悲观蔓延 | 观望企稳 |
| 40-60 | 中性 | 情绪均衡 | 让基本面主导 |
| 60-80 | 贪婪 | 乐观主导 | 减仓候选 |
| 80-100 | 极度贪婪 | FOMO散户涌入 | 逆向做空候选 |

### 3.4 散户 vs 机构情绪

```python
def classify_author_type(author: dict) -> str:
    """根据资料特征分类账号为散户/机构/KOL。

    Args:
        author: 含 followers_count / verified / account_age_days / post_count

    Returns:
        'institutional' / 'kol' / 'retail' / 'bot_risk'
    """
    followers = author.get("followers_count", 0)
    verified = author.get("verified", False)
    age_days = author.get("account_age_days", 0)
    post_count = author.get("post_count", 0)

    if age_days < 30 and post_count > 1000:
        return "bot_risk"
    if post_count > 0 and (post_count / max(age_days, 1)) > 50:
        return "bot_risk"
    if verified and followers > 100_000:
        return "institutional"
    if followers > 10_000:
        return "kol"
    return "retail"


def weighted_sentiment(df: pd.DataFrame, weights: dict | None = None) -> pd.Series:
    """按作者类别加权情绪。

    Args:
        df: 含 sentiment_score / author_type / timestamp
        weights: 类别权重，默认 institutional:3, kol:2, retail:1

    Returns:
        日度加权情绪时序
    """
    if weights is None:
        weights = {"institutional": 3.0, "kol": 2.0, "retail": 1.0, "bot_risk": 0.0}

    df["weight"] = df["author_type"].map(weights).fillna(1.0)
    df["weighted_sentiment"] = df["sentiment_score"] * df["weight"]

    return (
        df.groupby(df["timestamp"].dt.date)
        .apply(lambda g: g["weighted_sentiment"].sum() / g["weight"].sum())
        .rename("weighted_sentiment")
    )
```

---

## 4. 社媒信号作为因子

### 4.1 情绪因子构建与 IC/ICIR 检验

```python
from scipy.stats import spearmanr

def compute_ic(factor_series: pd.Series, forward_return: pd.Series, method: str = "spearman") -> float:
    """计算单期因子IC。

    Args:
        factor_series: 截面因子值（如同日情绪分）
        forward_return: 对应前向N日收益
        method: "spearman" 或 "pearson"

    Returns:
        IC [-1, 1]。|IC| > 0.05 有用，> 0.1 强。
    """
    aligned = pd.concat([factor_series, forward_return], axis=1).dropna()
    if len(aligned) < 5:
        return np.nan
    if method == "spearman":
        ic, _ = spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
    else:
        ic = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    return ic


def compute_icir(ic_series: pd.Series) -> float:
    """计算ICIR = mean(IC)/std(IC)。> 0.5 有用，> 1.0 强。"""
    return ic_series.mean() / ic_series.std() if ic_series.std() > 0 else np.nan
```

**IC/ICIR 评级**：
| 指标 | 弱 | 有用 | 强 |
|------|----|----|---|
| \|IC\| | < 0.03 | 0.03-0.08 | > 0.08 |
| ICIR | < 0.3 | 0.3-0.8 | > 0.8 |
| IC正率 | < 50% | 50-60% | > 60% |

### 4.2 对传统因子正交化

```python
def orthogonalize_sentiment(sentiment_factor: pd.Series, traditional_factors: pd.DataFrame) -> pd.Series:
    """对传统因子正交化情绪因子，保留纯情绪残差。

    Args:
        sentiment_factor: 截面标准化后的情绪因子
        traditional_factors: 传统因子矩阵（规模/动量/估值）

    Returns:
        去除共同成分后的纯情绪因子（残差）
    """
    from sklearn.linear_model import LinearRegression
    X = traditional_factors.fillna(0).values
    y = sentiment_factor.fillna(0).values
    reg = LinearRegression(fit_intercept=True).fit(X, y)
    residual = y - reg.predict(X)
    return pd.Series(residual, index=sentiment_factor.index)
```

### 4.3 跨平台情绪聚合权重

```python
PLATFORM_WEIGHTS = {
    # 权重依据：历史IC贡献 + 信息质量
    "guba": 0.45,        # 散户情绪温度计，覆盖最广
    "xueqiu": 0.30,      # 偏价值/趋势投资者，KOL多
    "weibo": 0.15,       # 事件脉冲，噪音大
    "forum": 0.10,       # 短线游资，题材热度
}

def aggregate_platform_sentiment(platform_scores: dict[str, float]) -> float:
    """多平台情绪加权聚合。

    Args:
        platform_scores: {platform_key: sentiment_score}，score in [-1, 1]

    Returns:
        聚合情绪分 [-1, 1]
    """
    total_weight = 0.0
    weighted_sum = 0.0
    for platform, score in platform_scores.items():
        weight = PLATFORM_WEIGHTS.get(platform, 0.05)
        weighted_sum += score * weight
        total_weight += weight
    return weighted_sum / total_weight if total_weight > 0 else 0.0
```

### 4.4 情绪反转信号

```python
def detect_sentiment_reversal(
    fg_index: pd.Series,
    price_series: pd.Series,
    extreme_threshold: float = 80.0,
    fear_threshold: float = 20.0,
    confirmation_days: int = 3,
) -> pd.DataFrame:
    """从情绪极值检测反转信号。

    Args:
        fg_index: 恐贪指数 [0, 100]
        price_series: 对应价格序列
        extreme_threshold: 极度贪婪阈值，默认80
        fear_threshold: 极度恐惧阈值，默认20
        confirmation_days: 确认天数

    Returns:
        DataFrame：signal(1=做空/-1=做多/0=无) / direction / strength

    Note:
        情绪反转通常滞后精确顶底3-10个交易日，需配合价格动量/成交量异常。
    """
    signals = pd.DataFrame(index=fg_index.index)
    signals["fg"] = fg_index
    signals["signal"] = 0
    signals["direction"] = ""
    signals["strength"] = 0.0

    greed_mask = (fg_index > extreme_threshold).rolling(confirmation_days).sum() == confirmation_days
    signals.loc[greed_mask, "signal"] = 1
    signals.loc[greed_mask, "direction"] = "short"
    signals.loc[greed_mask, "strength"] = (fg_index - extreme_threshold).clip(0) / 20

    fear_mask = (fg_index < fear_threshold).rolling(confirmation_days).sum() == confirmation_days
    signals.loc[fear_mask, "signal"] = -1
    signals.loc[fear_mask, "direction"] = "long"
    signals.loc[fear_mask, "strength"] = (fear_threshold - fg_index).clip(0) / 20

    return signals
```

---

## 5. 平台专项分析

### 5.1 股吧：散户情绪温度计 + 龙虎情绪

**热度异常检测**

```python
def detect_guba_heat_anomaly(posts: list[dict], baseline: float) -> dict:
    """检测股吧热度异常。

    Args:
        posts: get_a_share_social_sentiment 返回的帖子列表
        baseline: 历史日均帖子数

    Returns:
        {'heat_zscore': float, 'is_anomaly': bool, 'read_total': int}
    """
    read_total = sum(p.get("read_count") or 0 for p in posts)
    msg_count = len(posts)
    # 简化z-score：实际需历史序列
    heat_zscore = (msg_count - baseline) / max(baseline, 1) if baseline > 0 else 0
    return {
        "heat_zscore": heat_zscore,
        "is_anomaly": heat_zscore > 2.0,
        "read_total": read_total,
    }
```

**看多比例**

```python
def compute_bull_ratio(scored_posts: list[dict]) -> float:
    """计算看多比例（需先LLM打分）。

    Args:
        scored_posts: 已打分的帖子列表，含 sentiment_score

    Returns:
        看多比例 [0, 1]。> 0.8 极度乐观（警惕）。
    """
    if not scored_posts:
        return 0.0
    bull = sum(1 for p in scored_posts if p.get("sentiment_score", 0) > 0.1)
    return bull / len(scored_posts)
```

### 5.2 雪球：KOL一致性 + 机构观点扩散

**KOL一致性量化**

```python
def compute_kol_consensus(scored_posts: list[dict]) -> dict:
    """量化雪球KOL观点一致性。

    Args:
        scored_posts: 已打分且标注author_type的帖子

    Returns:
        {'consensus_score': float[-1,1], 'kol_count': int, 'agreement': float}
    """
    kol_posts = [p for p in scored_posts if p.get("author_type") == "kol"]
    if not kol_posts:
        return {"consensus_score": 0.0, "kol_count": 0, "agreement": 0.0}
    scores = [p["sentiment_score"] for p in kol_posts]
    avg = np.mean(scores)
    agreement = sum(1 for s in scores if (s > 0) == (avg > 0)) / len(scores)
    return {
        "consensus_score": avg,
        "kol_count": len(kol_posts),
        "agreement": agreement,
    }
```

### 5.3 微博：事件脉冲 + 传播速度

**事件情绪脉冲**

```python
def detect_weibo_pulse(posts: list[dict], window_minutes: int = 30) -> dict:
    """检测微博事件情绪脉冲。

    Args:
        posts: 微博帖子列表，含 timestamp / repost_count
        window_minutes: 脉冲检测窗口

    Returns:
        {'is_pulse': bool, 'peak_reposts': int, 'sentiment_shift': float}
    """
    if not posts:
        return {"is_pulse": False, "peak_reposts": 0, "sentiment_shift": 0.0}
    reposts = [p.get("repost_count", 0) for p in posts]
    peak = max(reposts)
    median = np.median(reposts)
    is_pulse = peak > median * 5 and peak > 100
    return {
        "is_pulse": is_pulse,
        "peak_reposts": peak,
        "sentiment_shift": 0.0,  # 需时序打分计算
    }
```

---

## 6. 数据 schema 与存储

### 6.1 统一采集接口

```python
from dataclasses import dataclass
from enum import Enum

class Platform(str, Enum):
    GUBA = "guba"
    XUEQIU = "xueqiu"
    WEIBO = "weibo"
    FORUM = "forum"

@dataclass
class ASocialMediaQuery:
    """A股社媒查询参数。"""
    platform: Platform
    code: str
    limit: int = 50
    include_sentiment: bool = True

def collect_a_share_social_signals(query: ASocialMediaQuery) -> dict:
    """A股社媒数据统一采集入口。

    Args:
        query: 查询参数

    Returns:
        标准化JSON：platform / items / metadata
    """
    collectors = {
        Platform.GUBA: _collect_guba,      # get_a_share_social_sentiment
        Platform.XUEQIU: _collect_xueqiu,  # read_url + 解析
        Platform.WEIBO: _collect_weibo,   # read_url + 解析
    }
    collector = collectors.get(query.platform)
    if not collector:
        raise ValueError(f"Unsupported platform: {query.platform}")
    raw_data = collector(query)
    if query.include_sentiment:
        raw_data = _enrich_with_sentiment(raw_data)  # LLM打分
    return raw_data
```

### 6.2 因子存储 schema

```sql
-- A股社媒情绪因子表（DuckDB / SQLite）
CREATE TABLE a_share_social_sentiment_factors (
    date        DATE NOT NULL,
    code        VARCHAR(20) NOT NULL,
    platform    VARCHAR(20) NOT NULL,      -- guba / xueqiu / weibo / forum
    sentiment   FLOAT,                     -- [-1, 1]
    buzz_zscore FLOAT,                     -- 热度Z-score
    fear_greed  FLOAT,                     -- [0, 100]
    msg_count   INTEGER,
    author_type VARCHAR(20),               -- institutional / kol / retail
    PRIMARY KEY (date, code, platform)
);
```

---

## 7. 注意事项与局限

1. **领先价值有限**：社媒情绪对宽指数IC约0.03-0.06，宜作辅助因子，非主因子。
2. **操纵风险**：股吧/微博存在水军/营销号，需来源质量评分过滤。
3. **语言适配**：VADER/FinBERT为英文模型，A股社媒为中文，须用LLM打分或中文词典。
4. **API成本控制**：LLM打分每条约500 token，批量打分需预算控制，高频用词典兜底。
5. **延迟vs质量**：实时采集噪音大，日聚合信号更干净，按策略周期选择。
6. **因子衰减**：社媒情绪因子有效性随参与者增多而衰减，定期重测IC。
7. **股吧端点稳定性**：股吧JSON端点历史变动，本技能采用HTML列表页（最稳定入口），解析逻辑需随页面改版维护。

---

## 附录：海外平台（多市场扩展参考，非A股默认）

> 以下平台为多市场扩展参考，**非A股默认数据源**。A股分析以股吧/雪球/微博为主。

| 平台 | 适用市场 | 采集方式 | 备注 |
|------|---------|---------|------|
| Twitter/X | 美股/加密 | 官方API v2（付费）或 ntscraper | FinTwit生态，$TICKER cashtag |
| Reddit | 美股/加密 | PRAW（免费API） | r/wallstreetbets meme股热度 |
| Telegram | 加密 | Telethon（MTProto） | 信号频道/研究推送/巨鲸警报 |
| Discord | 加密/量化 | discord.py（Bot API） | 项目社区健康度 |

海外平台情绪量化可用 VADER（英文短帖）/ FinBERT（财经文本）/ LLM（长复杂文本），详见历史版本。

---

*Version: v2.0 | Updated: 2026-07-28 | Scope: A股情绪驱动策略研究（非直接实盘信号）*
