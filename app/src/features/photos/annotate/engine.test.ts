import { describe, expect, it } from "vitest";
import {
  addShape,
  canRedo,
  canUndo,
  checkpoint,
  createState,
  deleteSelected,
  fromAuthoredShapes,
  type LayerShape,
  redo,
  replaceShapes,
  saveVersion,
  selectShape,
  undo,
} from "./engine";

const RECT: LayerShape = {
  id: "s1",
  kind: "rect",
  x: 0.1,
  y: 0.2,
  width: 0.3,
  height: 0.4,
  stroke: "#0f766e",
};
const ARROW: LayerShape = {
  id: "s2",
  kind: "arrow",
  from: { x: 0.7, y: 0.1 },
  to: { x: 0.5, y: 0.3 },
  stroke: "#0f766e",
};

describe("annotation history determinism", () => {
  it("undo and redo restore byte-identical shape sets", () => {
    const start = createState([RECT]);
    const added = addShape(start, ARROW);
    const undone = undo(added);
    expect(undone.shapes).toEqual([RECT]);
    expect(canUndo(undone)).toBe(false);
    expect(canRedo(undone)).toBe(true);

    const redone = redo(undone);
    expect(redone.shapes).toEqual([RECT, ARROW]);
    expect(canRedo(redone)).toBe(false);
    expect(redo(redone)).toEqual(redone);
  });

  it("undoing every step returns the authored layer exactly", () => {
    let state = createState([RECT]);
    state = addShape(state, ARROW);
    state = addShape(state, { ...ARROW, id: "s3" });
    state = undo(state);
    state = undo(state);
    expect(state.shapes).toEqual([RECT]);
    expect(state.past).toEqual([]);
    expect(state.future).toHaveLength(2);
  });

  it("repeating the same operation sequence is deterministic", () => {
    const run = () => {
      let state = createState();
      state = addShape(state, RECT);
      state = checkpoint(state);
      state = replaceShapes(state, [
        { ...RECT, width: 0.5, height: 0.6 },
      ]);
      state = addShape(state, ARROW);
      state = undo(state);
      return state.shapes;
    };
    expect(run()).toEqual(run());
  });

  it("keeps history entries immutable when a later shape is edited", () => {
    let state = createState([RECT]);
    state = checkpoint(state);
    const before = state.shapes;
    state = replaceShapes(state, [{ ...RECT, width: 0.9 }]);
    expect(before[0]).toEqual(RECT);
    expect(state.shapes).not.toBe(before);
    state = undo(state);
    expect(state.shapes).toEqual([RECT]);
  });

  it("undo after delete restores the shape and clears the selection", () => {
    let state = createState([RECT, ARROW]);
    state = selectShape(state, "s1");
    state = deleteSelected(state);
    expect(state.shapes).toEqual([ARROW]);
    expect(state.selectedId).toBeNull();
    state = undo(state);
    expect(state.shapes).toEqual([RECT, ARROW]);
  });

  it("does nothing when there is nothing to undo, redo or delete", () => {
    const state = createState([RECT]);
    expect(undo(state)).toBe(state);
    expect(redo(state)).toBe(state);
    expect(deleteSelected(state)).toBe(state);
  });

  it("copies freehand points instead of aliasing the caller's array", () => {
    const points = [{ x: 0.1, y: 0.1 }];
    const state = createState([
      {
        id: "f1",
        kind: "freehand",
        points,
        strokeWidth: 0.01,
        stroke: "#155e75",
      },
    ]);
    (points as Array<{ x: number; y: number }>).push({ x: 0.2, y: 0.2 });
    expect(state.shapes[0]?.kind).toBe("freehand");
    if (state.shapes[0]?.kind !== "freehand") throw new Error("shape");
    expect(state.shapes[0].points).toHaveLength(1);
  });
});

describe("versioned layers", () => {
  it("adds a pending version without touching earlier versions", () => {
    const first = saveVersion([], [RECT], { author: "u1", at: "2026-11-15T09:00:00Z" });
    expect(first).toHaveLength(1);
    expect(first[0]).toMatchObject({
      id: "LOCAL-ANV-1",
      versionNumber: 1,
      state: "pending",
      sample: true,
    });
    const shapesBefore = first[0]?.shapes;
    const second = saveVersion(first, [RECT, ARROW], {
      author: "u1",
      at: "2026-11-15T09:05:00Z",
    });
    expect(second).toHaveLength(2);
    expect(second[1]?.versionNumber).toBe(2);
    expect(second[0]?.shapes).toBe(shapesBefore);
    expect(second[0]?.shapes).toHaveLength(1);
  });

  it("numbers after the highest existing fixture version", () => {
    const fixture = [
      {
        id: "SAMPLE-ANV-1",
        versionNumber: 7,
        state: "committed" as const,
        shapes: [RECT],
        createdAt: "2026-11-01T00:00:00Z",
        author: "kitti",
        sample: true as const,
      },
    ];
    expect(
      saveVersion(fixture, [ARROW], { author: "u1", at: "2026-11-15T09:00:00Z" })[1]
        ?.versionNumber,
    ).toBe(8);
  });
});

describe("fixture layer seeding", () => {
  it("gives authored shapes stable ids in declaration order", () => {
    const seeded = fromAuthoredShapes(
      [
        {
          kind: "rect",
          x: 0.3,
          y: 0.2,
          width: 0.3,
          height: 0.4,
          stroke: "#0f766e",
        },
        {
          kind: "freehand",
          points: [{ x: 0.1, y: 0.1 }],
          strokeWidth: 0.01,
          stroke: "#155e75",
        },
      ],
      "SAMPLE-PH-1",
    );
    expect(seeded.map((shape) => shape.id)).toEqual([
      "SAMPLE-PH-1-1",
      "SAMPLE-PH-1-2",
    ]);
    expect(seeded.map((shape) => shape.kind)).toEqual(["rect", "freehand"]);
  });
});
