import type { BenchmarkData } from "../data/types";
import {
  createDefaultFilterState,
  getAllowedValues,
  getDataFilterValues,
  updateEmptyFilter,
  updateFilterValues,
  updateScenarioFilter,
} from "./schema";
import {
  FILTER_QUERY_KEYS,
  type FilterQueryIssue,
  type FilterQueryKey,
  type FilterQueryResult,
  type FilterState,
  type MultiFilterKey,
} from "./types";

const FILTER_QUERY_KEY_SET = new Set<string>(FILTER_QUERY_KEYS);

export function decodeFilterQuery(params: URLSearchParams, data: BenchmarkData): FilterQueryResult {
  const { scenarioIds } = getDataFilterValues(data);
  const issues: FilterQueryIssue[] = [];
  for (const key of params.keys()) {
    if (!FILTER_QUERY_KEY_SET.has(key)) {
      issues.push({
        code: "unknown_key",
        key,
        message: `未対応の filter query key です: ${key}`,
      });
    }
  }

  const scenarioValues = params.getAll("scenario");
  if (scenarioValues.length > 1) {
    issues.push({
      code: "repeated_scenario",
      key: "scenario",
      message: "scenario query は 1 件だけ指定できます。",
    });
  }
  validateValues("scenario", scenarioValues, scenarioIds, issues);

  const emptyValues = params.getAll("empty");
  if (emptyValues.length > 1) {
    issues.push({
      code: "repeated_empty",
      key: "empty",
      message: "empty query は 1 件だけ指定できます。",
    });
  }
  validateValues("empty", emptyValues, ["show"], issues);

  for (const key of FILTER_QUERY_KEYS.slice(2) as readonly MultiFilterKey[]) {
    validateValues(key, params.getAll(key), getAllowedValues(key, data), issues);
  }

  if (issues.length > 0) {
    return { ok: false, issues };
  }

  let state = createDefaultFilterState(data);
  if (scenarioValues.length === 1) {
    state = updateScenarioFilter(state, scenarioValues[0]!, data);
  }
  if (emptyValues.length === 1) {
    state = updateEmptyFilter(state, true);
  }
  for (const key of FILTER_QUERY_KEYS.slice(2) as readonly MultiFilterKey[]) {
    const values = params.getAll(key);
    if (values.length > 0) {
      state = updateFilterValues(state, key, values, data);
    }
  }

  return {
    ok: true,
    state,
    canonicalSearch: encodeFilterState(state, data),
  };
}

export function encodeFilterState(state: FilterState, data: BenchmarkData): string {
  const validatedState = normalizeFilterState(state, data);
  const params = new URLSearchParams();
  if (validatedState.scenario !== null) {
    params.append("scenario", validatedState.scenario);
  }
  if (validatedState.showEmpty) {
    params.append("empty", "show");
  }

  for (const key of FILTER_QUERY_KEYS.slice(2) as readonly MultiFilterKey[]) {
    const allowedValues = getAllowedValues(key, data);
    const selectedValues = validatedState[key] as ReadonlySet<string>;
    if (selectedValues.size === allowedValues.length) {
      continue;
    }
    for (const value of allowedValues) {
      if (selectedValues.has(value)) {
        params.append(key, value);
      }
    }
  }

  const query = params.toString();
  return query.length === 0 ? "" : `?${query}`;
}

function normalizeFilterState(state: FilterState, data: BenchmarkData): FilterState {
  let normalized = updateScenarioFilter(state, state.scenario, data);
  normalized = updateEmptyFilter(normalized, state.showEmpty);
  for (const key of FILTER_QUERY_KEYS.slice(2) as readonly MultiFilterKey[]) {
    normalized = updateFilterValues(normalized, key, normalized[key] as ReadonlySet<string>, data);
  }
  return normalized;
}

function validateValues(
  key: FilterQueryKey,
  values: readonly string[],
  allowedValues: readonly string[],
  issues: FilterQueryIssue[],
): void {
  const allowed = new Set(allowedValues);
  for (const value of values) {
    if (value.length === 0) {
      issues.push({
        code: "empty_value",
        key,
        value,
        message: `${key} query に空の値は指定できません。`,
      });
    } else if (!allowed.has(value)) {
      issues.push({
        code: "unknown_value",
        key,
        value,
        message: `${key} query に存在しない値が指定されています: ${value}`,
      });
    }
  }
}
