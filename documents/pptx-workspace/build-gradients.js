const sharp = require('sharp');

async function createGradient(name, w, h, stops) {
  const stopsXml = stops.map((s, i) =>
    `<stop offset="${i * 50}%" style="stop-color:${s}"/>`
  ).join('');
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">
    <defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      ${stopsXml}
    </linearGradient></defs>
    <rect width="100%" height="100%" fill="url(#g)"/>
  </svg>`;
  await sharp(Buffer.from(svg)).resize(w, h).png().toFile(name);
  console.log(`Created: ${name}`);
}

async function main() {
  // Title slide - red gradient
  await createGradient("bg-title.png", 1440, 810, ["#EE1832", "#B81328"]);
  // Dark slide backgrounds
  await createGradient("bg-dark.png", 1440, 810, ["#292929", "#1a1a1a"]);
  // Red sidebar gradient
  await createGradient("bg-red-sidebar.png", 510, 810, ["#EE1832", "#B81328"]);
  // Dashboard slide - red background
  await createGradient("bg-dashboard.png", 1440, 810, ["#EE1832", "#B81328"]);
  console.log("All gradient backgrounds created.");
}

main().catch(e => { console.error(e); process.exit(1); });
