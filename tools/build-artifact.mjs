#!/usr/bin/env node
/**
 * Construit dist/console-artifact.html : la console autonome, avec un
 * enregistrement du simulateur embarqué.
 *
 * Un artefact Claude fournit lui-même <!doctype>, <html>, <head> et <body> :
 * on n'extrait donc que le fragment entre les marqueurs, en réinjectant le
 * <title> et la feuille de polices. La console détecte la présence du
 * <script id="fixture"> et bascule seule en source « rejeu » — aucune
 * duplication de code entre la version connectée et la version hors ligne.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(root, 'web/index.html'), 'utf8');
const fixture = process.argv[2] || 'fixtures/02-saturation-asm.jsonl';
const jsonl = readFileSync(join(root, fixture), 'utf8').trim();

const B = '<!-- BEGIN CONSOLE -->', E = '<!-- END CONSOLE -->';
const a = src.indexOf(B), b = src.indexOf(E);
if (a < 0 || b < 0) { console.error('Marqueurs BEGIN/END CONSOLE introuvables'); process.exit(1); }

const title = (src.match(/<title>([^<]*)<\/title>/) || [, 'Console Tactique MFCL'])[1];
const fonts = (src.match(/<link rel="stylesheet" href="https:\/\/fonts\.googleapis[^>]*>/) || [''])[0];
const body = src.slice(a + B.length, b).trim();

// Le JSONL part dans un <script type="application/x-ndjson"> : le navigateur
// ne l'exécute pas, et il n'y a pas d'échappement JSON imbriqué à gérer.
// Seule la séquence </script> doit être neutralisée.
const safe = jsonl.replace(/<\/script/gi, '<\\/script');

// Le trait de côte suit le même chemin que l'enregistrement : embarqué s'il
// existe, absent sinon. La console le charge par le réseau quand elle est
// servie, depuis la balise quand elle ne l'est pas — un artefact autonome
// n'a droit à aucune requête.
const coastPath = join(root, 'web/coastline.json');
const coast = existsSync(coastPath)
  ? `<script id="coastline" type="application/json">\n` +
    readFileSync(coastPath, 'utf8').replace(/<\/script/gi, '<\\/script') +
    `\n</script>\n`
  : '';

mkdirSync(join(root, 'dist'), { recursive: true });
const out = `<title>${title}</title>\n${fonts}\n` + coast +
  `<script id="fixture" type="application/x-ndjson">\n${safe}\n</script>\n${body}\n`;
const dest = join(root, 'dist/console-artifact.html');
writeFileSync(dest, out);
console.log(`${dest} : ${(out.length / 1024 / 1024).toFixed(2)} Mo`);
