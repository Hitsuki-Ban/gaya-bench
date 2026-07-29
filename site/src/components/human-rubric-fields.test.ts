import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { HumanRubricFields } from "@/components/human-rubric-fields";

describe("HumanRubricFields", () => {
  it("選択中の値を primary 色で明示する", () => {
    const markup = renderToStaticMarkup(
      createElement(HumanRubricFields, {
        onChange() {},
        value: {
          content_correct: true,
          intent_match: 4,
          character_naturalness: 3,
          adoptable: false,
        },
      }),
    );

    expect(markup.match(/aria-pressed="true"/g)).toHaveLength(4);
    expect(markup.match(/bg-primary text-primary-foreground/g)).toHaveLength(4);
  });
});
