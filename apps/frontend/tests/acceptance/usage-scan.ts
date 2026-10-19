/**
 * Static sweep over the UI's class lists for semantic colour pairs. Each JSX
 * element is parsed with the TypeScript AST, so a pair can be formed across
 * elements: a semantic text slot is paired with the element's own background
 * or, failing that, the nearest ancestor that declares one — CSS inherits
 * colour and background down the tree. Conditional branches stay alternatives
 * (a branch's foreground is not crossed with another branch's background);
 * within one branch the background and foreground slots that co-occur are
 * crossed. Class lists returned by helpers and variant maps — strings outside
 * a `className` attribute — are scanned with the same-literal rule.
 *
 * Evidence boundary: this is a static model of the class lists, not a
 * rendered-DOM measurement — dynamic class construction and computed styles
 * are approximated. No pixel/computed-style arbitration is taken here or
 * anywhere else in this suite; the module reports declared-pair completeness
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

export interface PairUsage {
  key: string;
  background: string;
  alpha?: number;
  foreground: string;
  locations: string[];
}

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

type ConstantIndex = ReadonlyMap<string, ts.Expression>;

/** Strips the wrappers (`as const`, `satisfies`, parentheses) around a value. */
function unwrapExpression(node: ts.Expression): ts.Expression {
  let current = node;
  while (
    ts.isParenthesizedExpression(current) ||
    ts.isAsExpression(current) ||
    ts.isSatisfiesExpression(current)
  ) {
    current = current.expression;
  }
  return current;
}

/** Module-scope `const` initializers that template interpolations can name. */
function collectConstants(sourceFile: ts.SourceFile): ConstantIndex {
  const constants = new Map<string, ts.Expression>();
  for (const statement of sourceFile.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    if ((statement.declarationList.flags & ts.NodeFlags.Const) === 0) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (!ts.isIdentifier(declaration.name) || !declaration.initializer) {
        continue;
      }
      constants.set(declaration.name.text, declaration.initializer);
    }
  }
  return constants;
}

/**
 * Every value a known constant map/array can yield for `NAME[...]`. The index
 * expression is not evaluated, so each element is a possible alternative.
 */
function constantAlternatives(
  node: ts.ElementAccessExpression,
  constants: ConstantIndex,
): string[][] | undefined {
  if (!ts.isIdentifier(node.expression)) return undefined;
  const initializer = constants.get(node.expression.text);
  if (!initializer) return undefined;
  const target = unwrapExpression(initializer);
  if (ts.isObjectLiteralExpression(target)) {
    return target.properties.flatMap((property) =>
      ts.isPropertyAssignment(property)
        ? classAlternatives(property.initializer, constants)
        : [],
    );
  }
  if (ts.isArrayLiteralExpression(target)) {
    return target.elements.flatMap((element) =>
      classAlternatives(element, constants),
    );
  }
  return undefined;
}

/**
 * Every statically known combination of class strings a `className`
 * expression can contribute. Conditional branches become separate
 * alternatives; a `cn(...)` call crosses its arguments, since all of them
 * apply together. Template interpolations resolve through module-scope
 * constant maps/arrays, so `LEVEL_ICON_CLASS[level]` contributes each class
 * the map can yield instead of disappearing.
 */
function classAlternatives(
  node: ts.Node | undefined,
  constants: ConstantIndex,
): string[][] {
  if (!node) return [[]];
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    return [[node.text]];
  }
  if (ts.isTemplateExpression(node)) {
    let alternatives: string[][] = [[node.head.text]];
    for (const span of node.templateSpans) {
      alternatives = combine(
        alternatives,
        classAlternatives(span.expression, constants),
      );
      alternatives = combine(alternatives, [[span.literal.text]]);
    }
    return alternatives;
  }
  if (ts.isElementAccessExpression(node)) {
    return constantAlternatives(node, constants) ?? [[]];
  }
  if (ts.isJsxExpression(node)) {
    return classAlternatives(node.expression, constants);
  }
  if (ts.isParenthesizedExpression(node)) {
    return classAlternatives(node.expression, constants);
  }
  if (ts.isConditionalExpression(node)) {
    return [
      ...classAlternatives(node.whenTrue, constants),
      ...classAlternatives(node.whenFalse, constants),
    ];
  }
  if (ts.isBinaryExpression(node)) {
    const operator = node.operatorToken.kind;
    if (
      operator === ts.SyntaxKind.AmpersandAmpersandToken ||
      operator === ts.SyntaxKind.BarBarToken ||
      operator === ts.SyntaxKind.QuestionQuestionToken
    ) {
      return [[], ...classAlternatives(node.right, constants)];
    }
    return [
      ...classAlternatives(node.left, constants),
      ...classAlternatives(node.right, constants),
    ];
  }
  if (ts.isCallExpression(node)) {
    let combined: string[][] = [[]];
    for (const argument of node.arguments) {
      combined = combine(combined, classAlternatives(argument, constants));
    }
    return combined;
  }
  if (ts.isArrayLiteralExpression(node)) {
    let combined: string[][] = [[]];
    for (const element of node.elements) {
      combined = combine(combined, classAlternatives(element, constants));
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
  // Only real colour slots count; `text-inherit`/size utilities set no colour.
  if (
    foreground &&
    (foreground[1] === "white" || COLOR_TOKENS[foreground[1]] !== undefined)
  ) {
    state.foregrounds.add(foreground[1]);
  }
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
  private readonly constants: ConstantIndex;

  constructor(private readonly index: SourceIndex) {
    this.constants = collectConstants(index.sourceFile);
  }

  scan(): Map<string, PairUsage> {
    const visit = (node: ts.Node): void => {
      if (isJsxElement(node)) {
        const attribute = classAttribute(node);
        if (attribute?.initializer) {
          this.index.classNameRanges.push([
            attribute.getStart(this.index.sourceFile),
            attribute.getEnd(),
          ]);
          this.elementPairs(node, attribute);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(this.index.sourceFile);
    this.detachedLiteralPairs();
    return this.usage;
  }

  private alternativesOf(
    node: ts.JsxElement | ts.JsxSelfClosingElement,
  ): string[][] {
    const cached = this.elementAlternatives.get(node);
    if (cached) return cached;
    const attribute = classAttribute(node);
    const alternatives = classAlternatives(
      attribute?.initializer,
      this.constants,
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

export function scanSemanticPairs(): Map<string, PairUsage> {
  const usage = new Map<string, PairUsage>();
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
      const scanner = new Scanner({
        file,
        text,
        sourceFile,
        classNameRanges: [],
      });
      for (const [key, entry] of scanner.scan()) {
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
  }
  return usage;
}
