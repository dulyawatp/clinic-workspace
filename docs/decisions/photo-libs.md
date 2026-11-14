# Photo rendering libraries

Date checked: 2026-11-06

decision: adopt

For React 18, pin `react-konva` 18.2.16, `konva` 10.9.1 and `react-compare-slider` 4.0.0. The registry reports react-konva peers React and React DOM >=18 and Konva ^7.2.5, ^8.0.1, ^9.0.0 or ^10.0.0. react-compare-slider reports React and React DOM >=16.8. Konva's canvas and skia-canvas peers are optional server-side integrations, not browser rendering dependencies.

Official documentation: https://konvajs.org/docs/react/ and https://github.com/nerdyman/react-compare-slider. Konva's React documentation explicitly requires matching React and react-konva majors. Rendering libraries do not define annotation storage or authorize image access.

Registry checks: `pnpm view react-konva@18.2.16 peerDependencies version`, `pnpm view konva@10.9.1 peerDependencies version`, `pnpm view react-compare-slider@4.0.0 peerDependencies version`.
