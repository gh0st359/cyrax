import { ActionMatch } from '../types/common.js';

interface PatternSpec {
  kind: ActionMatch['kind'];
  pattern: RegExp;
}

const actionPatterns: PatternSpec[] = [
  { kind: 'execute', pattern: /\[EXECUTE\]\s*([\s\S]*?)\s*\[\/EXECUTE\]/g },
  { kind: 'write_file', pattern: /\[WRITE_FILE\s+path="([^"]+)"\]([\s\S]*?)\[\/WRITE_FILE\]/g },
  { kind: 'spawn', pattern: /\[SPAWN\s+type="(\w+)"\]([\s\S]*?)\[\/SPAWN\]/g },
  { kind: 'store', pattern: /\[STORE\s+category="(\w+)"\s+key="([^"]+)"\]([\s\S]*?)\[\/STORE\]/g },
  { kind: 'kill', pattern: /\[KILL\s+agent="([^"]+)"(?:\s+reason="([^"]*)")?\]/g },
  { kind: 'finding', pattern: /\[FINDING\s+severity="(\w+)"\s+title="([^"]+)"\]([\s\S]*?)\[\/FINDING\]/g },
  { kind: 'report', pattern: /\[REPORT\]([\s\S]*?)\[\/REPORT\]/g },
];

export function findAllActions(response: string): ActionMatch[] {
  const actions: ActionMatch[] = [];
  for (const spec of actionPatterns) {
    for (const match of response.matchAll(spec.pattern)) {
      actions.push({
        index: match.index ?? 0,
        kind: spec.kind,
        groups: match.slice(1).map((value) => value ?? ''),
        raw: match[0],
      });
    }
  }
  return actions.sort((left, right) => left.index - right.index);
}

export function findUnclosedTags(response: string): string[] {
  const paired: Array<[string, RegExp, RegExp]> = [
    ['EXECUTE', /\[EXECUTE\]/g, /\[\/EXECUTE\]/g],
    ['WRITE_FILE', /\[WRITE_FILE\b[^\]]*\]/g, /\[\/WRITE_FILE\]/g],
    ['SPAWN', /\[SPAWN\b[^\]]*\]/g, /\[\/SPAWN\]/g],
    ['STORE', /\[STORE\b[^\]]*\]/g, /\[\/STORE\]/g],
    ['FINDING', /\[FINDING\b[^\]]*\]/g, /\[\/FINDING\]/g],
    ['REPORT', /\[REPORT\]/g, /\[\/REPORT\]/g],
  ];
  const unclosed: string[] = [];
  for (const [name, opener, closer] of paired) {
    const opens = [...response.matchAll(opener)].length;
    const closes = [...response.matchAll(closer)].length;
    if (opens > closes) unclosed.push(`${name} (opened ${opens}x, closed ${closes}x — close every action tag exactly once)`);
  }
  return unclosed;
}
