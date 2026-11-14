import assert from 'node:assert/strict';
import { test } from 'node:test';
import { verifyPhotoDeps } from './verify-photo-deps.mjs';

const deps = { react: '^18.3.1', 'react-konva': '18.2.16', konva: '10.9.1', 'react-compare-slider': '4.0.0' };
const adr = 'decision: adopt\n`react-konva` 18.2.16, `konva` 10.9.1, `react-compare-slider` 4.0.0';
test('accepts recorded exact pins', () => assert.equal(verifyPhotoDeps({ dependencies: deps }, adr), true));
test('rejects wrong React major', () => assert.equal(verifyPhotoDeps({ dependencies: { ...deps, react: '^19.0.0' } }, adr), false));
test('rejects wrong react-konva major', () => assert.equal(verifyPhotoDeps({ dependencies: { ...deps, 'react-konva': '19.0.0' } }, adr), false));
test('rejects missing ADR', () => assert.equal(verifyPhotoDeps({ dependencies: deps }, null), false));
test('rejects ranged pins', () => assert.equal(verifyPhotoDeps({ dependencies: { ...deps, 'react-konva': '^18.2.16' } }, adr), false));
test('rejects version not recorded in ADR', () => assert.equal(verifyPhotoDeps({ dependencies: deps }, 'decision: adopt'), false));
