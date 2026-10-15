import schema from "../state-enums.schema.json";

const definitions = schema.$defs;

export type StateEnumName = keyof typeof definitions;
type StateEnumMap = Readonly<Record<StateEnumName, readonly string[]>>;

export const stateEnums = Object.freeze(
  Object.fromEntries(
    Object.entries(definitions).map(([name, definition]) => [
      name,
      Object.freeze([...definition.enum]),
    ]),
  ),
) as StateEnumMap;

export function getStateEnum(name: StateEnumName): readonly string[] {
  return stateEnums[name];
}

