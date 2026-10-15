import { describe, expect, it } from "vitest";

import schema from "../state-enums.schema.json";
import { stateEnums } from "./state-enums";

describe("stateEnums", () => {
  it("exposes every canonical enum without redefining its values", () => {
    expect(Object.keys(stateEnums).sort()).toEqual(Object.keys(schema.$defs).sort());
    for (const name of Object.keys(schema.$defs) as (keyof typeof schema.$defs)[]) {
      expect(stateEnums[name]).toEqual(schema.$defs[name].enum);
    }
  });
});

