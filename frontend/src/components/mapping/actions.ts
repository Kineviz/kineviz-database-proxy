import { projectService } from '../../services/projectService';
import {
  DirectionVerdict,
  GraphMappingDocument,
  MappingEnvelope,
  MappingTextResponse,
  PreviewRequest,
  PreviewResponse,
  PreviewSide,
} from '../../types/project';

/**
 * Every call the editor makes, in one place.
 *
 * Components ask for these instead of reaching for the axios client: the draft and its
 * problems live in the pane, and what a row wants is "ask the proxy whether this would
 * load", not "know how the URL is spelled". It is also where the two shapes a save can
 * take — the structured document, or the text a person pasted — sit next to each other, so
 * there is one place to look when the wire changes.
 */

export interface MappingActions {
  /** Ask whether this draft would load, without saving it. Answers path-named problems. */
  validate(document: GraphMappingDocument): Promise<MappingEnvelope>;
  /** The same for text a person pasted or uploaded. The proxy is the only YAML parser. */
  parseText(yaml: string): Promise<MappingEnvelope>;
  /** One save of the draft. */
  save(document: GraphMappingDocument): Promise<MappingEnvelope>;
  /** One save of text, which is how an uploaded file is applied. */
  saveText(yaml: string): Promise<MappingEnvelope>;
  /** Drop the override: the project goes back to pure auto-discovery. */
  reset(): Promise<MappingEnvelope>;
  /** What is stored, as text. The only YAML the browser is ever given. */
  text(): Promise<MappingTextResponse>;
  /** The annotated example the proxy ships. */
  example(): Promise<MappingTextResponse>;
  /** What discovery would propose, with its reasoning. Nothing is stored. */
  discover(): Promise<MappingEnvelope>;
  /** One edge pair, both directions, in one call. */
  preview(request: PreviewRequest): Promise<PreviewResponse>;
}

export const createActions = (project: string): MappingActions => ({
  validate: (document) => projectService.validateMapping(project, { mapping: document }),
  parseText: (yaml) => projectService.validateMapping(project, { yaml }),
  save: (document) => projectService.saveMapping(project, { model: document }),
  saveText: (yaml) => projectService.saveMapping(project, { yaml }),
  reset: () => projectService.saveMapping(project, { model: null }),
  text: () => projectService.getMappingText(project),
  example: () => projectService.getMappingExample(),
  discover: () => projectService.discoverMapping(project),
  preview: (request) => projectService.previewEdge(project, request),
});

/**
 * The four answers a direction check can give.
 *
 * A backwards link answers *nothing* rather than an error, so "no rows" is never a success
 * to be reported: it is read back as which direction joined nothing, and only then as a
 * verdict a person can act on.
 */
export const VERDICT_LABEL: Record<DirectionVerdict, string> = {
  settled: 'Settled',
  backwards: 'Backwards',
  'you decide': 'You decide',
  neither: 'Neither',
};

export const VERDICT_HELP: Record<DirectionVerdict, string> = {
  settled: 'Only this direction joins anything. The link means what the mapping says it means.',
  backwards:
    'This direction joins nothing — the other way round does. A link kept like this returns no rows rather than an error, so the graph simply never shows it.',
  'you decide':
    'Both directions join rows, so the database cannot settle it. Read the pair below and pick the sentence that is true.',
  neither:
    'The table has rows and neither direction joins anything to them, so at least one of the two key columns is the wrong one.',
};

/** One direction of the reply, or an empty one when the proxy did not answer that side. */
export const sideOf = (response: PreviewResponse, side: 'forwards' | 'backwards'): PreviewSide =>
  response[side] ?? {};

export const countOf = (side: PreviewSide): number =>
  side.count ?? side.rows?.length ?? 0;

/**
 * The verdict, from the two row counts.
 *
 * The counts already say it: both join, only this direction joins, only the other does, or
 * neither does. `settled` is the proxy's own summary of the same two counts and is only
 * consulted when the reply carries no rows at all, where "empty" must not be read as
 * success by itself.
 */
export const verdictOf = (response: PreviewResponse): DirectionVerdict => {
  // `verdict` is the plain spelling; `settled` is the field the route answers with today, and
  // a proxy that means the word rather than the flag puts it there.
  const stated = String(response.verdict ?? (typeof response.settled === 'string' ? response.settled : '') ?? '')
    .toLowerCase()
    .replace(/[_-]/g, ' ')
    .trim();
  const known: DirectionVerdict[] = ['settled', 'backwards', 'you decide', 'neither'];
  const match = known.find((verdict) => stated.replace(/\s/g, '') === verdict.replace(/\s/g, ''));
  if (match) return match;

  const forwards = countOf(sideOf(response, 'forwards'));
  const backwards = countOf(sideOf(response, 'backwards'));
  if (forwards && backwards) return 'you decide';
  if (forwards) return 'settled';
  if (backwards) return 'backwards';
  return 'neither';
};

/** The text a `?format=yaml` or example reply carries, whichever key it used. */
export const textOf = (response: MappingTextResponse): string => response.text ?? response.yaml ?? '';
