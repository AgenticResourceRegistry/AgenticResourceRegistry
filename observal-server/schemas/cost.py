# SPDX-FileCopyrightText: 2026 amogh-dongre <amoghdongre16@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Response models for /api/v1/exec/cost. All money is in USD."""

from typing import Literal

from pydantic import BaseModel

RangeKey = Literal["7d", "30d", "90d"]


class CostRange(BaseModel):
    key: RangeKey
    start: str
    end: str
    days: int
    timezone: str
    bucket: Literal["day", "week"]


class CostCurrency(BaseModel):
    default: str
    rates: dict[str, float]
    as_of: str | None


class CostKpis(BaseModel):
    spend_usd: float
    prev_spend_usd: float | None
    change_pct: float | None
    month_to_date_usd: float
    projected_month_usd: float | None
    active_users: int
    cost_per_active_user_usd: float | None
    sessions_priced: int
    cost_per_session_usd: float | None
    potential_savings_monthly_usd: float


class UnpricedModel(BaseModel):
    model: str
    usage_total: int  # all token types


class NotPricedHarness(BaseModel):
    harness: str
    display_name: str
    sessions: int
    credits: float
    reason: Literal["credits_only", "no_usage"]


class CostCoverage(BaseModel):
    sessions_total: int
    sessions_with_usage: int
    unpriced_models: list[UnpricedModel]
    not_priced: list[NotPricedHarness]


class ModelAmount(BaseModel):
    key: str  # model name, or "other"
    usd: float


class SeriesPoint(BaseModel):
    bucket_start: str
    total_usd: float
    by_model: list[ModelAmount]


class ModelShare(BaseModel):
    key: str  # model name, or "other"
    label: str
    usd: float
    share: float
    models: int


class HarnessSpend(BaseModel):
    harness: str
    display_name: str
    usd: float
    share: float


class CostOverview(BaseModel):
    range: CostRange
    currency: CostCurrency
    kpis: CostKpis
    coverage: CostCoverage
    series: list[SeriesPoint]
    model_share: list[ModelShare]
    harnesses: list[HarnessSpend]


class RouteDownSaving(BaseModel):
    kind: Literal["route_down"] = "route_down"
    counted: Literal[True] = True
    monthly_usd: float
    model: str
    model_label: str
    route_to: str
    route_to_label: str
    sessions: int
    model_sessions: int
    max_prompts: int
    cost_usd: float


class CachingSaving(BaseModel):
    kind: Literal["caching"] = "caching"
    counted: Literal[True] = True
    monthly_usd: float
    model: str
    model_label: str
    cache_hit: float
    org_cache_hit: float
    extra_cached_input: int  # input tokens that would have been cache reads
    input_price: float
    cache_read_price: float


class ReviewUser(BaseModel):
    user_id: str
    username: str
    departments: list[str]
    ratio_to_median: float


class ReviewUsersSaving(BaseModel):
    kind: Literal["review_users"] = "review_users"
    counted: Literal[False] = False
    monthly_usd: None = None
    users: list[ReviewUser]


class TokenCounts(BaseModel):
    input: int
    output: int
    cache_read: int
    cache_write: int


class TokenCosts(BaseModel):
    input: float
    output: float
    cache_read: float
    cache_write: float


class PersonSpend(BaseModel):
    user_id: str
    username: str
    sessions: int
    usd: float
    per_session_usd: float
    top_model: str
    top_model_label: str
    top_model_share: float
    cache_hit: float | None
    ratio_to_median: float | None
    flagged: bool


class RestOfDepartment(BaseModel):
    users: int
    sessions: int
    usd: float


class DepartmentSpend(BaseModel):
    name: str
    users: int
    sessions: int
    usd: float
    prev_usd: float | None
    change_pct: float | None
    per_user_usd: float | None
    median_per_session_usd: float | None
    people: list[PersonSpend]  # top 10 by spend
    rest: RestOfDepartment | None


class ModelHarness(BaseModel):
    harness: str
    display_name: str
    usd: float
    share: float


class ModelUser(BaseModel):
    user_id: str
    username: str
    departments: list[str]
    usd: float


class ModelBreakdown(BaseModel):
    model: str
    label: str
    provider: str
    priced: bool
    sessions: int
    usage: TokenCounts
    cost_usd: TokenCosts | None
    cache_hit: float | None
    usd: float | None
    share: float | None
    harnesses: list[ModelHarness]
    top_users: list[ModelUser]  # top 3


class CostTotals(BaseModel):
    sessions: int
    usage: TokenCounts
    cost_usd: TokenCosts
    cache_hit: float | None
    usd: float


class CostDetails(BaseModel):
    range: CostRange
    currency: CostCurrency
    savings: list[RouteDownSaving | CachingSaving | ReviewUsersSaving]
    departments: list[DepartmentSpend]
    multi_department_users: int
    models: list[ModelBreakdown]
    totals: CostTotals
