import { describe, expect, it } from "vite-plus/test";

import { buildComparisonModel } from "../comparison/model";
import type { BenchmarkData, Character, Clip, Line, Model, Scenario } from "../data/types";
import {
  createDefaultFilterState,
  decodeFilterQuery,
  encodeFilterState,
  projectComparisonModel,
  toggleFilterValue,
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

  it("schema/data 順で canonicalize し、重複値と入力順を正規化する", () => {
    const data = fixture();
    const result = decodeFilterQuery(
      new URLSearchParams(
        "model=beta&emotion=angry&gender=male&emotion=neutral&model=beta&gender=female",
      ),
      data,
    );

    expect(result.ok).toBe(true);
    if (!result.ok) {
      return;
    }
    expect(result.canonicalSearch).toBe(
      "?gender=female&gender=male&emotion=neutral&emotion=angry&model=beta",
    );
    expect(encodeFilterState(result.state, data)).toBe(result.canonicalSearch);
    expect([...result.state.emotion]).toEqual(["neutral", "angry"]);
  });

  it("scenario は 0/1 件だけ受け付け、unknown/empty query を明示的に拒否する", () => {
    const data = fixture();
    const result = decodeFilterQuery(
      new URLSearchParams("scenario=market&scenario=inn&gender=&age=ancient&unknown=value"),
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
    state = updateFilterValues(state, "model", ["beta"], data);

    expect(encodeFilterState(state, data)).toBe("?scenario=market&model=beta");
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
    expect(projection.models.map(({ id }) => id)).toEqual(["alpha", "beta"]);
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
  const vendor = character("vendor", "female", "adult");
  const guard = character("guard", "male", "middle_aged");
  const keeper = character("keeper", "neutral", "elderly");
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
  return {
    manifest: {
      format_version: 2,
      generated_at: "2026-07-28T00:00:00Z",
      models,
      clips: scenarios.flatMap((fixtureScenario) =>
        fixtureScenario.lines.flatMap((fixtureLine) =>
          models.map((fixtureModel) => clip(fixtureModel.id, fixtureScenario.id, fixtureLine.id)),
        ),
      ),
      failures: [],
    },
    scenarios,
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

function character(id: string, gender: Character["gender"], age: Character["age"]): Character {
  return { id, name: id, gender, age, voice: "自然な声" };
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

function clip(model: string, scenario: string, lineId: string): Clip {
  return {
    model,
    scenario,
    line: lineId,
    variant: "dry",
    path: `audio/${model}/${scenario}/${lineId}.opus`,
    duration_sec: 1,
    sha256: `${model}-${scenario}-${lineId}`,
    gen_params: {},
    rtf: 0.1,
  };
}
