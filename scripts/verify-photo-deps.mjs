import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const expected = { 'react-konva': '18.2.16', konva: '10.9.1', 'react-compare-slider': '4.0.0' };

export function verifyPhotoDeps(packageJson, adr) {
  const deps = packageJson.dependencies ?? {};
  const reactMajor = /^\^?(\d+)\./.exec(deps.react ?? '')?.[1];
  if (reactMajor !== '18' || !adr || !/^decision: adopt$/m.test(adr)) return false;
  return Object.entries(expected).every(([name, version]) =>
    deps[name] === version && adr.includes(`\`${name}\` ${version}`),
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const pkg = JSON.parse(readFileSync(new URL('../app/package.json', import.meta.url), 'utf8'));
    const adr = readFileSync(new URL('../docs/decisions/photo-libs.md', import.meta.url), 'utf8');
    if (!verifyPhotoDeps(pkg, adr)) throw new Error('Photo dependency pins or decision invalid');
    console.log('Photo dependency pins and decision verified');
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
