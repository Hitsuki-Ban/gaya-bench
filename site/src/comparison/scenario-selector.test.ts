import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vite-plus/test";

import { benchmarkData } from "@/data";
import { createDefaultFilterState, updateScenarioFilter, type FilterState } from "@/filters";

import { FilterToolbar } from "./filter-toolbar";
import { ScenarioContextLink } from "./scenario-context-link";
import { ScenarioSelector } from "./scenario-selector";

const ignoreChange = (_state: FilterState): void => {};

describe("scenario primary navigation", () => {
  it("場面selectorを唯一のscenario controlとして常時表示する", () => {
    const state = createDefaultFilterState(benchmarkData);
    const markup = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        null,
        createElement(
          Fragment,
          null,
          createElement(ScenarioSelector, {
            state,
            search: "",
            onChange: ignoreChange,
          }),
          createElement(FilterToolbar, {
            state,
            filteredRows: 161,
            totalRows: 161,
            onChange: ignoreChange,
            onReset: () => {},
          }),
        ),
      ),
    );

    expect(markup.match(/<select/g)).toHaveLength(1);
    expect(markup).toContain('id="scenario-selector"');
    expect(markup).toContain("すべてのシナリオ");
    expect(markup).toContain(`全${benchmarkData.scenarios.length}シナリオ`);
    expect(markup).not.toContain("<span>シナリオ</span>");
  });

  it("長い現在名・canonical search・場面詳細linkを省略せず表示する", () => {
    const scenario = benchmarkData.scenarios.find(({ id }) => id === "guild-hall");
    if (scenario === undefined) {
      throw new Error("guild-hall scenario fixture がありません。");
    }
    const state = updateScenarioFilter(
      createDefaultFilterState(benchmarkData),
      scenario.id,
      benchmarkData,
    );
    const search = `?scenario=${scenario.id}`;
    const markup = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        null,
        createElement(ScenarioSelector, {
          state,
          search,
          onChange: ignoreChange,
        }),
      ),
    );

    expect(markup).toContain(scenario.title);
    expect(markup).toContain(scenario.scene.setting);
    expect(markup).toContain(`href="/scenario/${scenario.id}?scenario=${scenario.id}"`);
    expect(markup).toContain(`value="${scenario.id}" selected=""`);
  });

  it("各カード・行用の場面linkは長い名称を折り返せる独立headerにする", () => {
    const scenario = benchmarkData.scenarios[0];
    if (scenario === undefined) {
      throw new Error("scenario fixture がありません。");
    }
    const longScenario = {
      ...scenario,
      title: `${scenario.title}・長い場面名でもカード幅からはみ出さず表示する`,
    };
    const markup = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        null,
        createElement(ScenarioContextLink, {
          density: "card",
          scenario: longScenario,
          search: "?scenario=sample",
        }),
      ),
    );

    expect(markup).toContain(longScenario.title);
    expect(markup).toContain("break-words");
    expect(markup).toContain(`href="/scenario/${scenario.id}?scenario=sample"`);
    expect(markup).toContain(`${longScenario.title}の場面詳細を見る`);
  });

  it("scenarioだけ選択中なら次級絞り込みのresetを無効にする", () => {
    const scenario = benchmarkData.scenarios[0];
    if (scenario === undefined) {
      throw new Error("scenario fixture がありません。");
    }
    const state = updateScenarioFilter(
      createDefaultFilterState(benchmarkData),
      scenario.id,
      benchmarkData,
    );
    const markup = renderToStaticMarkup(
      createElement(FilterToolbar, {
        state,
        filteredRows: scenario.lines.length,
        totalRows: 161,
        onChange: ignoreChange,
        onReset: () => {},
      }),
    );

    expect(markup).toContain("絞り込みを戻す");
    expect(markup).toContain("disabled");
  });
});
