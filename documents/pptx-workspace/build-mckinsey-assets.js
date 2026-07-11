/**
 * Build McKinsey-style gradient PNGs and icon assets
 * McKinsey color palette: deep teal #004D43, warm gray #9B9B9B, light gray #F5F5F5
 */
const fs = require('fs');
const path = require('path');
const React = require('react');
const ReactDOMServer = require('react-dom/server');

// Check if sharp is available from render-env
let sharp;
try {
  sharp = require('/Users/jasonkoo/Library/Application Support/aura-workshop/bundled-deps/render-env/node_modules/sharp');
} catch {
  try { sharp = require('sharp'); } catch {
    console.log('sharp not available, skipping gradient generation');
    process.exit(0);
  }
}

const OUT = __dirname + '/slides';

// McKinsey teal color
const TEAL = '#004D43';
const TEAL_LIGHT = '#E8F0EE';
const TEAL_MID = '#007A6B';
const GRAY = '#9B9B9B';
const GRAY_LIGHT = '#F5F5F5';
const DARK = '#1A1A1A';
const RED = '#C0392B';

// Small decorative icons as SVG data URIs for use in slides
function generateIconSvg(name, color = TEAL) {
  const icons = {
    forecast: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 16l4-8 4 4 4-6"/></svg>`,
    detect: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>`,
    validate: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
    act: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
    dashboard: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>`,
    localize: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>`,
    clock: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
    trending: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>`,
    dollar: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg>`,
    users: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg>`,
    server: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>`,
    target: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>`,
  };
  return icons[name] || icons.forecast;
}

async function build() {
  console.log('Building McKinsey-style gradient assets...');
  
  // Generate sidebar gradient (dark teal)
  await sharp({
    create: { width: 240, height: 405, channels: 3, background: { r: 0, g: 77, b: 67 } }
  }).png().toFile(OUT + '/mck-sidebar.png');
  
  // Generate light teal accent bar
  await sharp({
    create: { width: 6, height: 405, channels: 3, background: { r: 0, g: 122, b: 107 } }
  }).png().toFile(OUT + '/mck-accent-bar.png');
  
  // Generate light divider
  await sharp({
    create: { width: 720, height: 3, channels: 3, background: { r: 224, g: 224, b: 224 } }
  }).png().toFile(OUT + '/mck-divider.png');

  // Generate thin teal rule
  await sharp({
    create: { width: 60, height: 4, channels: 3, background: { r: 0, g: 77, b: 67 } }
  }).png().toFile(OUT + '/mck-rule.png');

  // Generate icon PNGs
  const iconNames = ['forecast', 'detect', 'validate', 'act', 'dashboard', 'localize', 'clock', 'trending', 'dollar', 'users', 'server', 'target'];
  for (const name of iconNames) {
    const svg = generateIconSvg(name, '#004D43');
    const svgBuffer = Buffer.from(svg);
    await sharp(svgBuffer).resize(36, 36).png().toFile(OUT + `/icon-${name}.png`);
    // Also red version
    const redSvg = generateIconSvg(name, '#C0392B');
    const redBuffer = Buffer.from(redSvg);
    await sharp(redBuffer).resize(36, 36).png().toFile(OUT + `/icon-${name}-red.png`);
    // Also white version  
    const whiteSvg = generateIconSvg(name, '#FFFFFF');
    const whiteBuffer = Buffer.from(whiteSvg);
    await sharp(whiteBuffer).resize(36, 36).png().toFile(OUT + `/icon-${name}-white.png`);
    // Dark version
    const darkSvg = generateIconSvg(name, '#1A1A1A');
    const darkBuffer = Buffer.from(darkSvg);
    await sharp(darkBuffer).resize(36, 36).png().toFile(OUT + `/icon-${name}-dark.png`);
  }

  console.log('Done building McKinsey assets.');
}

build().catch(e => { console.error(e); process.exit(1); });
