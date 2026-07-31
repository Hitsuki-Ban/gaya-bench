import { describe, expect, it } from "vite-plus/test";

import { buildComparisonModel } from "../comparison/model";
import type {
  ArtifactOutcome,
  BenchmarkData,
  Candidate,
  Character,
  Line,
  Model,
  Scenario,
} from "../data/types";
import {
  createDefaultFilterState,
  decodeFilterQuery,
  encodeFilterState,
  projectComparisonModel,
  resetNarrowingFilters,
  toggleFilterValue,
  updateEmptyFilter,
  updateFilterValues,
  updateScenarioFilter,
} from ".";

describe("filter query codec", () => {
  it("既定値を空の query として round-trip する", () => {
    const data = fixture();
    const state = createDefaultFilterState(data);

    expect(encodeFilterState(state, data)).toBe("");
    expect(decodeFilterQuery(new URLSearchParams(), data)).toEqual({
      ok: true,
      state,
      canonicalSearch: "",
    });
  });

  it("scenario を canonical URL から復元し、切替と絞り込み初期化でも唯一の状態源にする", () => {
    const data = fixture();
    const restored = decodeFilterQuery(new URLSearchParams("scenario=market&emotion=angry"), data);
    expect(restored.ok).toBe(true);
    if (!restored.ok) {
      return;
    }
    expect(restored.state.scenario).toBe("market");
    expect(restored.canonicalSearch).toBe("?scenario=market&emotion=angry");

    const switched = updateScenarioFilter(restored.state, "inn", data);
    expect(encodeFilterState(switched, data)).toBe("?scenario=inn&emotion=angry");

    const resetNarrowing = resetNarrowingFilters(switched, data);
    expect(resetNarrowing.scenario).toBe("inn");
    expect(encodeFilterState(resetNarrowing, data)).toBe("?scenario=inn");
    expect(encodeFilterState(updateScenarioFilter(resetNarrowing, null, data), data)).toBe("");
  });

  it("schema/data 順で canonicalize し、重複値と入力順を正規化する", () => {
    const data = fixture();
    const result = decodeFilterQuery(
      new URLSearchParams(
        "model=beta&emotion=angry&kind=machine&gender=male&emotion=neutral&model=beta&gender=female",
      ),
      data,
    );

    expect(result.ok).toBe(true);
    if (!result.ok) {
      return;
    }
    expect(result.canonicalSearch).toBe(
      "?kind=machine&gender=female&gender=male&emotion=neutral&emotion=angry&model=beta",
    );
    expect(encodeFilterState(result.state, data)).toBe(result.canonicalSearch);
    expect([...result.state.kind]).toEqual(["machine"]);
    expect([...result.state.emotion]).toEqual(["neutral", "angry"]);
  });

  it("未収録表示は明示 query のときだけ有効になる", () => {
    const data = fixture();
    const state = updateEmptyFilter(createDefaultFilterState(data), true);

    expect(encodeFilterState(state, data)).toBe("?empty=show");
    expect(decodeFilterQuery(new URLSearchParams("empty=show"), data)).toEqual({
      ok: true,
      state,
      canonicalSearch: "?empty=show",
    });
  });

  it("scenario は 0/1 件だけ受け付け、unknown/empty query を明示的に拒否する", () => {
    const data = fixture();
    const result = decodeFilterQuery(
      new URLSearchParams(
        "scenario=market&scenario=inn&kind=other&gender=&age=ancient&unknown=value",
      ),
      data,
    );

    expect(result).toEqual({
      ok: false,
      issues: [
        {
          code: "unknown_key",
          key: "unknown",
          message: "未対応の filter query key です: unknown",
        },
        {
          code: "repeated_scenario",
          key: "scenario",
          message: "scenario query は 1 件だけ指定できます。",
        },
        {
          code: "unknown_value",
          key: "kind",
          value: "other",
          message: "kind query に存在しない値が指定されています: other",
        },
        {
          code: "empty_value",
          key: "gender",
          value: "",
          message: "gender query に空の値は指定できません。",
        },
        {
          code: "unknown_value",
          key: "age",
          value: "ancient",
          message: "age query に存在しない値が指定されています: ancient",
        },
      ],
    });
  });

  it("helper は最低 1 値を守り、scenario/model を data と照合する", () => {
    const data = fixture();
    let state = createDefaultFilterState(data);
    state = updateScenarioFilter(state, "market", data);
    state = updateFilterValues(state, "kind", ["machine"], data);
    state = updateFilterValues(state, "model", ["beta"], data);

    expect([...state.kind]).toEqual(["machine"]);
    expect([...state.model]).toEqual(["beta"]);
    expect(encodeFilterState(state, data)).toBe("?scenario=market&kind=machine&model=beta");
    expect(() => toggleFilterValue(state, "kind", "machine", data)).toThrow(
      "kind filter は最低 1 件",
    );
    expect(() => toggleFilterValue(state, "model", "beta", data)).toThrow(
      "model filter は最低 1 件",
    );
    expect(() => updateScenarioFilter(state, "missing", data)).toThrow("存在しない scenario");
    expect(() => updateFilterValues(state, "model", ["missing"], data)).toThrow("存在しない値");
  });
});

describe("comparison projection", () => {
  it("複合条件を stable model 上へ適用し、base rowIndex と data 順を保つ", () => {
    const data = fixture();
    const model = buildComparisonModel(data);
    let state = createDefaultFilterState(data);
    state = updateScenarioFilter(state, "market", data);
    state = updateFilterValues(state, "kind", ["machine"], data);
    state = updateFilterValues(state, "gender", ["female"], data);
    state = updateFilterValues(state, "age", ["adult"], data);
    state = updateFilterValues(state, "emotion", ["angry"], data);
    state = updateFilterValues(state, "difficulty", ["hard"], data);
    state = updateFilterValues(state, "model", ["beta"], data);

    const projection = projectComparisonModel(model, state);

    expect(projection.rows.map(({ row }) => row.line.id)).toEqual(["vendor-2"]);
    expect(projection.rows.map(({ rowIndex }) => rowIndex)).toEqual([1]);
    expect([...projection.rowIndexes]).toEqual([1]);
    expect(projection.models.map(({ id }) => id)).toEqual(["beta"]);
    expect([...projection.modelIds]).toEqual(["beta"]);
    expect(projection.rows[0]?.row).toBe(model.rows[1]);
  });

  it("合法な組み合わせが 0 行でも空 projection を返す", () => {
    const data = fixture();
    const model = buildComparisonModel(data);
    let state = createDefaultFilterState(data);
    state = updateScenarioFilter(state, "inn", data);
    state = updateFilterValues(state, "emotion", ["angry"], data);

    const projection = projectComparisonModel(model, state);

    expect(projection.rows).toEqual([]);
    expect(projection.rowIndexes.size).toBe(0);
    expect(projection.models).toEqual([]);

    const reset = resetNarrowingFilters(state, data);
    expect(encodeFilterState(reset, data)).toBe("?scenario=inn");
    expect(projectComparisonModel(model, reset).rows.map(({ row }) => row.line.id)).toEqual([
      "keeper-1",
    ]);
  });

  it("kind の選択を projection key と行投影へ反映する", () => {
    const data = fixture();
    const model = buildComparisonModel(data);
    const defaultProjection = projectComparisonModel(model, createDefaultFilterState(data));
    const state = updateFilterValues(createDefaultFilterState(data), "kind", ["human"], data);
    const projection = projectComparisonModel(model, state);

    expect(projection.rows.map(({ row }) => row.line.id)).toEqual(["guard-1"]);
    expect(projection.key).not.toBe(defaultProjection.key);
  });

  it("既定では音声のない行を隠し、明示指定で再表示する", () => {
    const data = fixture();
    const partialData = {
      ...data,
      outcomes: data.outcomes.filter(
        (outcome) => !(outcome.group.scenario === "inn" && outcome.group.line === "keeper-1"),
      ),
    };
    const model = buildComparisonModel(partialData);
    const defaultState = createDefaultFilterState(partialData);

    expect(
      projectComparisonModel(model, defaultState).rows.map(({ row }) => row.line.id),
    ).not.toContain("keeper-1");
    expect(
      projectComparisonModel(model, updateEmptyFilter(defaultState, true)).rows.map(
        ({ row }) => row.line.id,
      ),
    ).toContain("keeper-1");
  });

  it("外部で組み立てられた不正 state を fail fast で拒否する", () => {
    const data = fixture();
    const model = buildComparisonModel(data);
    const state = {
      ...createDefaultFilterState(data),
      scenario: "missing",
    };

    expect(() => projectComparisonModel(model, state)).toThrow("scenario filter に存在しない値");
  });
});

function fixture(): BenchmarkData {
  const vendor = character("vendor", "machine", "female", "adult");
  const guard = character("guard", "human", "male", "middle_aged");
  const keeper = character("keeper", "spirit", "neutral", "elderly");
  const scenarios = [
    scenario(
      "market",
      [vendor, guard],
      [
        line("guard-1", "guard", "neutral", "standard"),
        line("vendor-1", "vendor", "cheerful", "standard"),
        line("vendor-2", "vendor", "angry", "hard"),
      ],
    ),
    scenario("inn", [keeper], [line("keeper-1", "keeper", "neutral", "standard")]),
  ];
  const models = [ttsModel("alpha"), ttsModel("beta")];
  const candidates = scenarios.flatMap((fixtureScenario) =>
    fixtureScenario.lines.flatMap((fixtureLine) =>
      models.map((fixtureModel) => candidate(fixtureModel.id, fixtureScenario.id, fixtureLine.id)),
    ),
  );
  const outcomes: ArtifactOutcome[] = candidates.map((item) => ({
    kind: "selected",
    group: {
      model: item.model,
      scenario: item.scenario,
      line: item.line,
      variant: item.variant,
    },
    candidate: item,
  }));
  return {
    release: {
      format_version: 4,
      generated_at: "2026-07-30T00:00:00Z",
      candidate_set_sha256: "d".repeat(64),
      models,
    },
    scenarios,
    outcomes,
    generation_profiles: [],
    credits: { model_sources: [], reference_voices: [] },
  };
}

function scenario(id: string, characters: readonly Character[], lines: readonly Line[]): Scenario {
  return {
    format_version: 1,
    id,
    title: id,
    locale: "ja",
    scene: { setting: "テスト" },
    characters,
    lines,
  };
}

function character(
  id: string,
  kind: Character["kind"],
  gender: Character["gender"],
  age: Character["age"],
): Character {
  return { id, name: id, kind, gender, age, voice: "自然な声" };
}

function line(
  id: string,
  characterId: string,
  emotion: Line["emotion"],
  difficulty: Line["difficulty"],
): Line {
  return {
    id,
    character: characterId,
    text: id,
    emotion,
    intensity: 2,
    delivery: "自然に",
    difficulty,
    loop_ok: true,
    final_intonation: "fall",
  };
}

function ttsModel(id: string): Model {
  return {
    id,
    name: id,
    version: "1",
    license_note: "テスト",
    capabilities: {
      emotion: false,
      voice_prompt: false,
      clone: false,
      nonverbal: false,
      reading: false,
    },
  };
}

function candidate(model: string, scenario: string, lineId: string): Candidate {
  return {
    model,
    scenario,
    line: lineId,
    variant: "dry",
    take_index: 1,
    take_id: "a".repeat(64),
    path: `audio/takes/${model}/${scenario}/${lineId}/dry/take-0001-${"b".repeat(64)}.opus`,
    duration_sec: 1,
    sha256: "b".repeat(64),
    generation_input_sha256: "c".repeat(64),
    gen_params: {
      seed: 1,
      recipe_version: "test-v1",
      sampling: {},
      requested: {},
      realized: {},
    },
    rtf: 0.1,
    loudness: {
      source: "encoded_opus",
      i_lufs: -18,
      tp_dbtp: -1,
      shortfall: false,
    },
    gate: {
      mechanical: "pass",
      content: "review_required",
      policy_version: "test-v1",
    },
  };
}
