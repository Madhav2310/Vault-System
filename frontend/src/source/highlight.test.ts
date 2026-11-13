import { describe, expect, it } from "vitest";
import { pageLabel, sectionLabel, splitHighlight } from "./highlight";

const text = "One. Two is here. Three!";
const sentences = [
  [0, 4],
  [5, 17],
  [18, 24],
];

describe("splitHighlight", () => {
  it("highlights exactly the offset range [start, end)", () => {
    expect(splitHighlight(text, sentences, 1, 2)).toEqual([
      { text: "One. ", highlighted: false },
      { text: "Two is here.", highlighted: true },
      { text: " Three!", highlighted: false },
    ]);
  });
  it("highlights several sentences as one run", () => {
    const parts = splitHighlight(text, sentences, 0, 2);
    expect(parts.filter((p) => p.highlighted).map((p) => p.text)).toEqual(["One. Two is here."]);
  });
  it("never highlights by string match: duplicate text elsewhere stays plain", () => {
    const t = "Same. Same.";
    const s = [
      [0, 5],
      [6, 11],
    ];
    expect(splitHighlight(t, s, 1, 2)).toEqual([
      { text: "Same. ", highlighted: false },
      { text: "Same.", highlighted: true },
    ]);
  });
  it("converts code-point offsets with astral characters", () => {
    const t = "\u{1F600} Hi. Yo.";
    const s = [
      [0, 5],
      [6, 9],
    ];
    expect(splitHighlight(t, s, 1, 2).find((p) => p.highlighted)?.text).toBe("Yo.");
  });
  it("falls back to plain text for empty or invalid ranges", () => {
    expect(splitHighlight(text, sentences, 2, 2)).toEqual([{ text, highlighted: false }]);
    expect(splitHighlight(text, sentences, 0, 9)).toEqual([{ text, highlighted: false }]);
    expect(splitHighlight(text, [[0, 99]], 0, 1)).toEqual([{ text, highlighted: false }]);
    expect(splitHighlight(text, ["x"], 0, 1)).toEqual([{ text, highlighted: false }]);
  });
  it("omits empty edge parts", () => {
    expect(splitHighlight("Hi.", [[0, 3]], 0, 1)).toEqual([{ text: "Hi.", highlighted: true }]);
  });
});

describe("pageLabel", () => {
  it("is null for flow documents (0, empty, null), never page 0", () => {
    expect(pageLabel([0, 0])).toBeNull();
    expect(pageLabel([])).toBeNull();
    expect(pageLabel(null)).toBeNull();
  });
  it("labels one page and ranges", () => {
    expect(pageLabel([3, 3])).toBe("Page 3");
    expect(pageLabel([3, 4])).toBe("Pages 3–4");
    expect(pageLabel([0, 2])).toBe("Page 2");
  });
});

describe("sectionLabel", () => {
  it("joins the path and omits empty", () => {
    expect(sectionLabel(["8", "Termination"])).toBe("8 › Termination");
    expect(sectionLabel([])).toBeNull();
  });
});
