const sharp = require('sharp');
const React = require('react');
const ReactDOMServer = require('react-dom/server');
const {
  FaChartLine, FaExclamationTriangle, FaCheckCircle, FaBell, FaDatabase, FaGlobeAsia,
  FaShieldAlt, FaRocket, FaStore, FaClipboardList, FaCogs, FaChartBar, FaProjectDiagram
} = require('react-icons/fa');

async function rasterizeIcon(Icon, color, size, filename) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(Icon, { color: `#${color}`, size: String(size) })
  );
  await sharp(Buffer.from(svg)).resize(size, size).png().toFile(filename);
  console.log(`  created ${filename}`);
}

async function createGradient(name, w, h, stops, deg = 135) {
  const stopsXml = stops.map((s, i) =>
    `<stop offset="${i * 50}%" style="stop-color:${s}"/>`
  ).join('');
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">
    <defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      ${stopsXml}
    </linearGradient></defs>
    <rect width="100%" height="100%" fill="url(#g)"/>
  </svg>`;
  await sharp(Buffer.from(svg)).png().toFile(name);
  console.log(`  created ${name}`);
}

async function main() {
  // Icons
  await rasterizeIcon(FaChartLine, "EE1832", 256, "icons/icon-forecast.png");
  await rasterizeIcon(FaExclamationTriangle, "EE1832", 256, "icons/icon-alert.png");
  await rasterizeIcon(FaCheckCircle, "EE1832", 256, "icons/icon-validate.png");
  await rasterizeIcon(FaBell, "D4A84B", 256, "icons/icon-notify.png");
  await rasterizeIcon(FaDatabase, "EE1832", 256, "icons/icon-data.png");
  await rasterizeIcon(FaGlobeAsia, "D4A84B", 256, "icons/icon-globe.png");
  await rasterizeIcon(FaShieldAlt, "292929", 256, "icons/icon-shield.png");
  await rasterizeIcon(FaRocket, "EE1832", 256, "icons/icon-rocket.png");
  await rasterizeIcon(FaStore, "292929", 256, "icons/icon-store.png");
  await rasterizeIcon(FaClipboardList, "D4A84B", 256, "icons/icon-clipboard.png");
  await rasterizeIcon(FaCogs, "292929", 256, "icons/icon-cogs.png");
  await rasterizeIcon(FaChartBar, "EE1832", 256, "icons/icon-chart.png");
  await rasterizeIcon(FaProjectDiagram, "D4A84B", 256, "icons/icon-flow.png");

  // Gradients
  await createGradient("bg-red-diagonal.png", 1440, 810, ["#EE1832", "#B81328"]);
  await createGradient("bg-dark.png", 1440, 810, ["#292929", "#1a1a1a"]);
  await createGradient("bg-warm.png", 1440, 810, ["#F9F5F0", "#EFE9E2"]);
  await createGradient("bg-redtop.png", 1440, 810, ["#EE1832", "#F9F5F0"]);

  console.log("All assets created.");
}

main().catch(e => { console.error(e); process.exit(1); });
