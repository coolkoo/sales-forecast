const pptxgen = require('pptxgenjs');
const html2pptx = require('/Users/jasonkoo/Library/Application Support/aura-workshop/skills/pptx/scripts/html2pptx.js');
const path = require('path');

const SLIDES_DIR = path.join(__dirname, 'slides');

async function main() {
  const pptx = new pptxgen();
  pptx.layout = 'LAYOUT_16x9';
  pptx.author = 'KFC Vietnam Operations & Analytics';
  pptx.title = 'Faster Sales Anomaly Detection & Forecasting';

  const slideFiles = [
    'slide01-title.html',
    'slide02-problem.html',
    'slide03-cost.html',
    'slide04-what.html',
    'slide05-beforeafter.html',
    'slide06-pipeline.html',
    'slide07-forecast.html',
    'slide08-anomaly.html',
    'slide09-action.html',
    'slide10-dashboard.html',
    'slide11-infra.html',
    'slide12-value.html',
    'slide13-roadmap.html',
    'slide14-summary.html',
  ];

  for (const file of slideFiles) {
    const filePath = path.join(SLIDES_DIR, file);
    console.log(`Processing ${file}...`);
    const { slide } = await html2pptx(filePath, pptx);
    console.log(`  Slide created: ${slide.name || file}`);
  }

  const outputPath = path.join(__dirname, 'kfc-vietnam-sales-forecast-platform.pptx');
  await pptx.writeFile({ fileName: outputPath });
  console.log(`
Presentation saved to: ${outputPath}`);
}

main().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
