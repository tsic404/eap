/**
 * Static sweep over the UI's class lists and inline `style` colour values for
 * semantic colour pairs. Each JSX element is parsed with the TypeScript AST, so
 * a pair can be formed across elements: a semantic text slot is paired with the
 * element's own background or, failing that, the nearest ancestor that declares
 * one — CSS inherits colour and background down the tree. Conditional branches
 * stay alternatives (a branch's foreground is not crossed with another branch's
 * background); within one branch the background and foreground slots that
 * co-occur are crossed. Class lists returned by helpers and variant maps —
 * strings outside a `className` attribute — are scanned with the same-literal
 * rule. An inline `var(--color-<slot>)` counts as that slot; a colour literal
 * written straight into a style object is reported by `scanInlineColours`.
 *
 * Evidence boundary: this is a static model of class lists and style-object
 * literals, not a rendered-DOM measurement — dynamic class construction,
 * computed styles and colour-bearing SVG attributes (`stroke` / `fill`) are
 * approximated or unmodelled. No pixel/computed-style arbitration is taken here
 * or anywhere else in this suite; the module reports declared-pair completeness
 * over the static model only.
 */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import * as ts from "typescript";

import { parseColor } from "./contrast";
import { COLOR_TOKENS } from "./declared-pairs";
import { FRONTEND_ROOT } from "./paths";

const SCAN_DIRECTORIES = ["app", "components"];
const STRING_LITERAL = /"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g;
const SEMANTIC_BACKGROUND = /^bg-([a-z-]+?)(?:\/(\d+))?$/;
const SEMANTIC_FOREGROUND = /^text-([a-z-]+)$/;
const TEST_FILE = /\.test\.tsx?$/;
const CLASS_LIKE_PROPERTY = /^(?:bg|text)-/;
const MAX_ALTERNATIVES = 64;
const STYLE_ATTRIBUTE = "style";
const COLOUR_PROPERTY =
  /^(?:color|background|backgroundColor|border|borderColor|borderTop|borderRight|borderBottom|borderLeft|outline|fill|stroke)$/;
const SLOT_REFERENCE = /var\(\s*--color-([a-z0-9-]+)\s*(?:,\s*([^)]*?)\s*)?\)/g;
/** Colour literals whose function notation carries whitespace of its own. */
const COLOUR_LITERAL = /#[0-9a-f]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\([^)]*\)/gi;
/** Words a colour-bearing CSS shorthand may legitimately carry besides a colour. */
const NON_COLOUR_KEYWORD: Readonly<Record<string, true>> = {
  auto: true,
  "border-box": true,
  center: true,
  contain: true,
  "content-box": true,
  cover: true,
  currentcolor: true,
  dashed: true,
  dotted: true,
  double: true,
  fixed: true,
  groove: true,
  hidden: true,
  inherit: true,
  initial: true,
  inset: true,
  local: true,
  medium: true,
  "no-repeat": true,
  none: true,
  outset: true,
  "padding-box": true,
  repeat: true,
  "repeat-x": true,
  "repeat-y": true,
  revert: true,
  ridge: true,
  round: true,
  scroll: true,
  solid: true,
  space: true,
  thick: true,
  thin: true,
  transparent: true,
  unset: true,
};

export interface PairUsage {
  key: string;
  background: string;
  alpha?: number;
  foreground: string;
  locations: string[];
}

export interface InlineColourReference {
  /** `style` property the value was written under. */
  property: string;
  value: string;
  location: string;
}

export interface InlineSlotReference extends InlineColourReference {
  /** `--color-<token>` the value references. */
  token: string;
  /** Literal the reference falls back to when the stylesheet is absent. */
  fallback?: string;
}

type InlineColourEntry = InlineColourReference | InlineSlotReference;

export interface InlineColourSweep {
  /** Colour literals written into a style object without a slot reference. */
  hardcoded: InlineColourReference[];
  /** Slot references, in source order, for the fallback lockstep check. */
  slots: InlineSlotReference[];
}

export type InlineColourReading =
  | { kind: "slot"; token: string; fallback?: string }
  | { kind: "hardcoded"; value: string };

interface BackgroundSlot {
  token: string;
  alpha?: number;
}

interface ClassState {
  backgrounds: Map<string, BackgroundSlot>;
  foregrounds: Set<string>;
}

interface SourceIndex {
  file: string;
  text: string;
  sourceFile: ts.SourceFile;
  classNameRanges: Array<[number, number]>;
}

interface InlineDeclaration {
  attribute?: ts.JsxAttribute;
  /** Slot utilities the inline colours are equivalent to, for pair forming. */
  utilities: string[];
  references: InlineColourEntry[];
}

function* classFiles(directory: string): Generator<string> {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      yield* classFiles(fullPath);
    } else if (entry.name.endsWith(".tsx") && !TEST_FILE.test(entry.name)) {
      yield fullPath;
    }
  }
}

function combine(left: string[][], right: string[][]): string[][] {
  return left
    .flatMap((first) => right.map((second) => [...first, ...second]))
    .slice(0, MAX_ALTERNATIVES);
}

/**
 * Every statically known combination of class strings a `className`
 * expression can contribute. Conditional branches become separate
 * alternatives; a `cn(...)` call crosses its arguments, since all of them
 * apply together.
 */
function classAlternatives(node: ts.Node | undefined): string[][] {
  if (!node) return [[]];
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    return [[node.text]];
  }
  if (ts.isTemplateExpression(node)) {
    const parts = [
      node.head.text,
      ...node.templateSpans.map((span) => span.literal.text),
    ];
    return [[parts.join(" ")]];
  }
  if (ts.isJsxExpression(node)) return classAlternatives(node.expression);
  if (ts.isParenthesizedExpression(node)) {
    return classAlternatives(node.expression);
  }
  if (ts.isConditionalExpression(node)) {
    return [
      ...classAlternatives(node.whenTrue),
      ...classAlternatives(node.whenFalse),
    ];
  }
  if (ts.isBinaryExpression(node)) {
    const operator = node.operatorToken.kind;
    if (
      operator === ts.SyntaxKind.AmpersandAmpersandToken ||
      operator === ts.SyntaxKind.BarBarToken ||
      operator === ts.SyntaxKind.QuestionQuestionToken
    ) {
      return [[], ...classAlternatives(node.right)];
    }
    return [
      ...classAlternatives(node.left),
      ...classAlternatives(node.right),
    ];
  }
  if (ts.isCallExpression(node)) {
    let combined: string[][] = [[]];
    for (const argument of node.arguments) {
      combined = combine(combined, classAlternatives(argument));
    }
    return combined;
  }
  if (ts.isArrayLiteralExpression(node)) {
    let combined: string[][] = [[]];
    for (const element of node.elements) {
      combined = combine(combined, classAlternatives(element));
    }
    return combined;
  }
  if (ts.isObjectLiteralExpression(node)) {
    const alternatives: string[][] = [[]];
    for (const property of node.properties) {
      if (!ts.isPropertyAssignment(property)) continue;
      const name = property.name;
      const text = ts.isIdentifier(name) ? name.text : name.getText();
      if (CLASS_LIKE_PROPERTY.test(text)) {
        alternatives.push([text]);
      }
    }
    return alternatives;
  }
  return [[]];
}

function slotKey(slot: BackgroundSlot): string {
  return slot.alpha === undefined ? slot.token : `${slot.token}/${slot.alpha}`;
}

function addStateToken(
  state: ClassState,
  utility: string,
): void {
  const background = utility.match(SEMANTIC_BACKGROUND);
  if (background && COLOR_TOKENS[background[1]]) {
    const slot: BackgroundSlot = {
      token: background[1],
      alpha: background[2] === undefined ? undefined : Number(background[2]),
    };
    state.backgrounds.set(slotKey(slot), slot);
    return;
  }
  const foreground = utility.match(SEMANTIC_FOREGROUND);
  if (foreground) state.foregrounds.add(foreground[1]);
}

function readStates(classLists: readonly string[]): Map<string, ClassState> {
  const states = new Map<string, ClassState>();
  for (const classList of classLists) {
    for (const raw of classList.split(/\s+/)) {
      if (raw === "") continue;
      const parts = raw.split(":");
      const utility = parts.pop() ?? "";
      const variant = parts.join(":");
      const state = states.get(variant) ?? {
        backgrounds: new Map(),
        foregrounds: new Set(),
      };
      addStateToken(state, utility);
      if (state.backgrounds.size > 0 || state.foregrounds.size > 0) {
        states.set(variant, state);
      }
    }
  }
  return states;
}

function directlyRendersText(node: ts.JsxElement | ts.JsxFragment): boolean {
  return node.children.some((child) => {
    if (ts.isJsxText(child)) return child.text.trim() !== "";
    if (ts.isJsxExpression(child)) return child.expression !== undefined;
    if (ts.isJsxFragment(child)) return directlyRendersText(child);
    return false;
  });
}

function isJsxElement(
  node: ts.Node,
): node is ts.JsxElement | ts.JsxSelfClosingElement {
  return ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node);
}

function classAttribute(
  node: ts.JsxElement | ts.JsxSelfClosingElement,
): ts.JsxAttribute | undefined {
  const attributes = ts.isJsxElement(node)
    ? node.openingElement.attributes
    : node.attributes;
  return attributes.properties.find(
    (property): property is ts.JsxAttribute =>
      ts.isJsxAttribute(property) &&
      ts.isIdentifier(property.name) &&
      property.name.text === "className",
  );
}

function isColourLiteral(part: string): boolean {
  if (NON_COLOUR_KEYWORD[part.toLowerCase()]) return false;
  try {
    parseColor(part);
    return true;
  } catch {
    // A colour name carries no unit or parenthesis, so an unlisted word inside
    // a colour-bearing property cannot be anything else.
    return /^[a-z]+$/i.test(part);
  }
}

/**
 * Reads the colours one inline `style` value carries: `var()` references to
 * stylesheet slots, plus literals written without one. A literal is admissible
 * only as a slot reference's fallback, where it must restate that slot's
 * current value (C-83).
 */
export function readInlineColour(value: string): InlineColourReading[] {
  const readings: InlineColourReading[] = [];
  let remainder = "";
  let cursor = 0;
  for (const match of value.matchAll(SLOT_REFERENCE)) {
    const [, token, fallback] = match;
    remainder += value.slice(cursor, match.index);
    cursor = match.index + match[0].length;
    readings.push(
      fallback === undefined
        ? { kind: "slot", token }
        : { kind: "slot", token, fallback },
    );
  }
  remainder += value.slice(cursor);
  const literals: string[] = [];
  const rest = remainder.replace(COLOUR_LITERAL, (literal) => {
    literals.push(literal);
    return " ";
  });
  for (const literal of literals) {
    readings.push({ kind: "hardcoded", value: literal });
  }
  for (const part of rest.split(/\s+/)) {
    if (part !== "" && isColourLiteral(part)) {
      readings.push({ kind: "hardcoded", value: part });
    }
  }
  return readings;
}

function isSlotReference(
  reference: InlineColourReference,
): reference is InlineSlotReference {
  return "token" in reference;
}

/** Normalizes `text-white` to the background family's foreground slot. */
export function normalizeForeground(
  token: string,
  background: string,
): string | undefined {
  if (token === "inherit") return undefined;
  if (token !== "white") return COLOR_TOKENS[token] ? token : undefined;
  const familyForeground = `${background.split("-")[0]}-foreground`;
  const value = COLOR_TOKENS[familyForeground];
  if (!value) return "white";
  const color = parseColor(value);
  return color.a === 1 && color.r === 1 && color.g === 1 && color.b === 1
    ? familyForeground
    : "white";
}

class Scanner {
  private readonly usage = new Map<string, PairUsage>();
  private readonly elementAlternatives = new WeakMap<
    ts.JsxElement | ts.JsxSelfClosingElement,
    string[][]
  >();
  private readonly elementInline = new WeakMap<
    ts.JsxElement | ts.JsxSelfClosingElement,
    InlineDeclaration
  >();
  /** Inline `style` colours in source order; complete once `scan()` returned. */
  readonly inlineColours: InlineColourEntry[] = [];

  constructor(private readonly index: SourceIndex) {}

  scan(): Map<string, PairUsage> {
    const visit = (node: ts.Node): void => {
      if (isJsxElement(node)) {
        const inline = this.inlineDeclaration(node);
        this.inlineColours.push(...inline.references);
        const attribute = classAttribute(node);
        if (attribute?.initializer) {
          this.index.classNameRanges.push([
            attribute.getStart(this.index.sourceFile),
            attribute.getEnd(),
          ]);
        }
        const declared = attribute ?? inline.attribute;
        if (declared?.initializer) this.elementPairs(node, declared);
      }
      ts.forEachChild(node, visit);
    };
    visit(this.index.sourceFile);
    this.detachedLiteralPairs();
    return this.usage;
  }

  /**
   * The inline `style` colours of one element: the slot utilities they are
   * equivalent to, plus the references the tie-back sweep reports.
   */
  private inlineDeclaration(
    element: ts.JsxElement | ts.JsxSelfClosingElement,
  ): InlineDeclaration {
    const cached = this.elementInline.get(element);
    if (cached) return cached;
    const attributes = ts.isJsxElement(element)
      ? element.openingElement.attributes
      : element.attributes;
    const attribute = attributes.properties.find(
      (property): property is ts.JsxAttribute =>
        ts.isJsxAttribute(property) &&
        ts.isIdentifier(property.name) &&
        property.name.text === STYLE_ATTRIBUTE,
    );
    const declaration: InlineDeclaration = {
      attribute,
      utilities: [],
      references: [],
    };
    this.elementInline.set(element, declaration);
    let node: ts.Node | undefined = attribute?.initializer;
    if (node && ts.isJsxExpression(node)) node = node.expression;
    while (node && ts.isParenthesizedExpression(node)) node = node.expression;
    if (attribute === undefined || node === undefined) return declaration;
    if (!ts.isObjectLiteralExpression(node)) return declaration;
    const line =
      this.index.sourceFile.getLineAndCharacterOfPosition(
        attribute.getStart(this.index.sourceFile),
      ).line + 1;
    const location = `${path.relative(FRONTEND_ROOT, this.index.file)}:${line}`;
    for (const property of node.properties) {
      if (!ts.isPropertyAssignment(property)) continue;
      const name = property.name;
      if (!ts.isIdentifier(name) && !ts.isStringLiteral(name)) continue;
      if (!COLOUR_PROPERTY.test(name.text)) continue;
      const value = property.initializer;
      if (
        !ts.isStringLiteral(value) &&
        !ts.isNoSubstitutionTemplateLiteral(value)
      ) {
        continue;
      }
      for (const reading of readInlineColour(value.text)) {
        if (reading.kind === "hardcoded") {
          declaration.references.push({
            property: name.text,
            value: reading.value,
            location,
          });
          continue;
        }
        declaration.references.push({
          property: name.text,
          value: value.text,
          token: reading.token,
          fallback: reading.fallback,
          location,
        });
        if (name.text === "color") {
          declaration.utilities.push(`text-${reading.token}`);
        } else if (
          name.text === "background" ||
          name.text === "backgroundColor"
        ) {
          declaration.utilities.push(`bg-${reading.token}`);
        }
      }
    }
    return declaration;
  }

  private alternativesOf(
    node: ts.JsxElement | ts.JsxSelfClosingElement,
  ): string[][] {
    const cached = this.elementAlternatives.get(node);
    if (cached) return cached;
    const attribute = classAttribute(node);
    const utilities = this.inlineDeclaration(node).utilities;
    const alternatives = classAlternatives(attribute?.initializer).map(
      (classes) =>
        utilities.length > 0 ? [...classes, utilities.join(" ")] : classes,
    );
    this.elementAlternatives.set(node, alternatives);
    return alternatives;
  }

  private inheritedContexts(from: ts.Node): Array<{
    background?: BackgroundSlot;
    foreground?: string;
  }> {
    const ancestors: Array<ts.JsxElement | ts.JsxSelfClosingElement> = [];
    let current = from.parent;
    while (current) {
      if (isJsxElement(current)) ancestors.push(current);
      current = current.parent;
    }
    let contexts: Array<{
      background?: BackgroundSlot;
      foreground?: string;
    }> = [{}];
    for (const ancestor of ancestors.reverse()) {
      const next: typeof contexts = [];
      for (const context of contexts) {
        for (const alternative of this.alternativesOf(ancestor)) {
          const base = readStates(alternative).get("");
          const backgrounds = base ? [...base.backgrounds.values()] : [];
          const foregrounds = base ? [...base.foregrounds] : [];
          next.push({
            background:
              backgrounds.length > 0
                ? backgrounds[backgrounds.length - 1]
                : context.background,
            foreground:
              foregrounds.length > 0
                ? foregrounds[foregrounds.length - 1]
                : context.foreground,
          });
        }
      }
      contexts = next.slice(0, MAX_ALTERNATIVES);
    }
    return contexts;
  }

  private elementPairs(
    element: ts.JsxElement | ts.JsxSelfClosingElement,
    attribute: ts.JsxAttribute,
  ): void {
    const line =
      this.index.sourceFile.getLineAndCharacterOfPosition(
        attribute.getStart(this.index.sourceFile),
      ).line + 1;
    const location = `${path.relative(FRONTEND_ROOT, this.index.file)}:${line}`;
    const rendersText = ts.isJsxElement(element)
      ? directlyRendersText(element)
      : false;
    for (const alternative of this.alternativesOf(element)) {
      const states = readStates(alternative);
      const hasOwnForeground = [...states.values()].some(
        (state) => state.foregrounds.size > 0,
      );
      if (states.size > 0 && !hasOwnForeground && !rendersText) continue;
      if (states.size === 0 && !rendersText) continue;
      const contexts = this.inheritedContexts(element);
      const base = states.get("");
      if (states.size === 0) {
        for (const context of contexts) {
          const background = context.background ?? { token: "background" };
          const foreground = context.foreground ?? "foreground";
          const normalized = normalizeForeground(foreground, background.token);
          if (normalized === undefined) continue;
          this.addPair(background, normalized, location);
        }
        continue;
      }
      for (const state of states.values()) {
        const backgrounds =
          state.backgrounds.size > 0
            ? [...state.backgrounds.values()]
            : base && base.backgrounds.size > 0
              ? [...base.backgrounds.values()]
              : undefined;
        const foregrounds =
          state.foregrounds.size > 0
            ? [...state.foregrounds]
            : base && base.foregrounds.size > 0
              ? [...base.foregrounds]
              : undefined;
        for (const context of contexts) {
          const effectiveBackgrounds =
            backgrounds ??
            (context.background ? [context.background] : [{ token: "background" }]);
          const effectiveForegrounds =
            foregrounds ??
            (context.foreground ? [context.foreground] : ["foreground"]);
          for (const background of effectiveBackgrounds) {
            for (const foreground of effectiveForegrounds) {
              const normalized = normalizeForeground(
                foreground,
                background.token,
              );
              if (normalized === undefined) continue;
              this.addPair(background, normalized, location);
            }
          }
        }
      }
    }
  }

  private detachedLiteralPairs(): void {
    for (const match of this.index.text.matchAll(STRING_LITERAL)) {
      const classList = match[1] ?? match[2] ?? match[3] ?? "";
      if (!classList.includes("bg-") && !classList.includes("text-")) continue;
      const start = match.index ?? 0;
      if (
        this.index.classNameRanges.some(
          ([from, to]) => start >= from && start < to,
        )
      ) {
        continue;
      }
      const line = this.index.text.slice(0, start).split("\n").length;
      const location = `${path.relative(FRONTEND_ROOT, this.index.file)}:${line}`;
      for (const state of readStates([classList]).values()) {
        if (state.foregrounds.size === 0) continue;
        const slot =
          [...state.backgrounds.values()][0] ?? { token: "background" };
        for (const foreground of state.foregrounds) {
          const normalized = normalizeForeground(foreground, slot.token);
          if (normalized === undefined) continue;
          this.addPair(slot, normalized, location);
        }
      }
    }
  }

  private addPair(
    background: BackgroundSlot,
    foreground: string,
    location: string,
  ): void {
    const key = `${slotKey(background)}|${foreground}`;
    const existing = this.usage.get(key);
    if (existing) {
      if (!existing.locations.includes(location)) {
        existing.locations.push(location);
      }
      return;
    }
    this.usage.set(key, {
      key,
      background: background.token,
      alpha: background.alpha,
      foreground,
      locations: [location],
    });
  }
}

function* sourceIndices(): Generator<SourceIndex> {
  for (const directory of SCAN_DIRECTORIES) {
    for (const file of classFiles(path.join(FRONTEND_ROOT, directory))) {
      const text = readFileSync(file, "utf8");
      const sourceFile = ts.createSourceFile(
        file,
        text,
        ts.ScriptTarget.Latest,
        true,
        ts.ScriptKind.TSX,
      );
      yield { file, text, sourceFile, classNameRanges: [] };
    }
  }
}

/** Every semantic pair the class lists and inline styles can form (C-83). */
export function scanSemanticPairs(): Map<string, PairUsage> {
  const usage = new Map<string, PairUsage>();
  for (const index of sourceIndices()) {
    for (const [key, entry] of new Scanner(index).scan()) {
      const existing = usage.get(key);
      if (existing) {
        for (const location of entry.locations) {
          if (!existing.locations.includes(location)) {
            existing.locations.push(location);
          }
        }
      } else {
        usage.set(key, entry);
      }
    }
  }
  return usage;
}

/** Inline `style` colours: slot references, and literals written without one. */
export function scanInlineColours(): InlineColourSweep {
  const references: InlineColourEntry[] = [];
  for (const index of sourceIndices()) {
    const scanner = new Scanner(index);
    scanner.scan();
    references.push(...scanner.inlineColours);
  }
  return {
    hardcoded: references.filter((reference) => !isSlotReference(reference)),
    slots: references.filter(isSlotReference),
  };
}
